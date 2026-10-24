package rendercontrol

import (
	"context"
	"errors"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type CoordinatorConfig struct {
	Target      Target
	Instance    string
	Owner       string
	Lease       time.Duration
	CallTimeout time.Duration
}

type Coordinator struct {
	store  *Store
	client *controlclient.Client
	config CoordinatorConfig
}

func (*Coordinator) String() string   { return "rendercontrol.Coordinator{redacted}" }
func (*Coordinator) GoString() string { return "rendercontrol.Coordinator{redacted}" }

func NewCoordinator(store *Store, client *controlclient.Client, config CoordinatorConfig) (*Coordinator, error) {
	if config.Lease == 0 {
		config.Lease = 90 * time.Second
	}
	if config.CallTimeout == 0 {
		config.CallTimeout = 15 * time.Second
	}
	if store == nil || client == nil || !gatewayPattern.MatchString(config.Target.gatewayID) || client.GatewayID() != config.Target.gatewayID || !client.HTTPS() || !instancePattern.MatchString(config.Instance) || !labelPattern.MatchString(config.Owner) || config.CallTimeout < time.Millisecond || config.CallTimeout > time.Minute || config.Lease < 2*config.CallTimeout+20*time.Second || config.Lease > 5*time.Minute {
		return nil, ErrInvalid
	}
	return &Coordinator{store: store, client: client, config: config}, nil
}

func recoveryErrorCode(err error) string {
	var call *controlclient.CallError
	if errors.As(err, &call) {
		return "gateway_call_failed"
	}
	switch {
	case errors.Is(err, ErrFence):
		return "stale_recovery_lease"
	case errors.Is(err, ErrCipher):
		return "request_unavailable"
	case errors.Is(err, ErrConflict):
		return "recovery_conflict"
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return "recovery_interrupted"
	default:
		return "recovery_storage_failed"
	}
}

func closeAfterAdmissionError(err error) bool {
	var call *controlclient.CallError
	if !errors.As(err, &call) {
		return false
	}
	switch call.HTTPStatus {
	case 400, 403, 404, 410, 413, 415, 422:
		return true
	}
	return false
}

// Step claims exactly one due intent. It owns no in-memory work queue, sleeps
// nowhere and opens no transaction across a remote call. Hosts schedule Steps.
func (c *Coordinator) Step(ctx context.Context) (bool, error) {
	ctx, cancel := context.WithTimeout(ctx, c.config.Lease-5*time.Second)
	defer cancel()
	leases, err := c.store.ClaimRecovery(ctx, c.config.Target, c.config.Instance, c.config.Owner, 1, c.config.Lease)
	if err != nil || len(leases) == 0 {
		return false, err
	}
	lease := leases[0]
	closing := lease.closing
	delay, advanceErr := c.advance(ctx, lease, &closing)
	if errors.Is(advanceErr, ErrFence) {
		return true, advanceErr
	}
	code := ""
	if advanceErr != nil {
		code, delay = recoveryErrorCode(advanceErr), RecoveryBackoff(lease.failures)
	}
	if err = c.store.FinishRecovery(ctx, lease, code, closing, delay); err != nil {
		return true, err
	}
	return true, advanceErr
}

func (c *Coordinator) advance(ctx context.Context, lease RecoveryLease, closing *bool) (time.Duration, error) {
	i, err := c.store.GuardRecovery(ctx, lease)
	if err != nil || i.Terminal() {
		return 0, err
	}
	if *closing && i.State == "reserved" && i.DispatchCount == 0 {
		_, err = c.store.ReleaseRecoveryUnsent(ctx, lease)
		return 0, err
	}
	if !*closing && i.State == "reserved" {
		return c.dispatch(ctx, lease, closing)
	}
	identity, err := i.RequestIdentity()
	if err != nil {
		return 0, err
	}
	callCtx, cancel := context.WithTimeout(ctx, c.config.CallTimeout)
	var resolution controlclient.RequestResolution
	if *closing {
		resolution, err = c.client.CloseRequest(callCtx, identity)
	} else {
		resolution, err = c.client.LookupRequest(callCtx, identity)
	}
	cancel()
	if err != nil {
		return 0, err
	}
	switch resolution.Status {
	case "closed":
		proof, ok := resolution.ClosureProof()
		if !ok {
			return 0, ErrConflict
		}
		_, err = c.store.ReleaseRecoveryClosed(ctx, lease, proof)
		return 0, err
	case "unknown":
		if *closing || i.AdmissionID != "" || i.RemoteJobID != "" {
			return 0, ErrConflict
		}
		return c.dispatch(ctx, lease, closing)
	case "admitted":
		admission := *resolution.Admission // validated by the concrete client
		bound, err := c.store.BindRecoveryAdmission(ctx, lease, admission.ID, admission.JobID)
		if err != nil || bound.Terminal() {
			return 0, err
		}
		if admission.Status != "pending" {
			*closing = false
			if lease.cancelling && admission.Status == "confirmed" {
				if _, err := c.store.GuardRecovery(ctx, lease); err != nil {
					return 0, err
				}
				subject, err := controlclient.NewSubject(bound.Instance, bound.UserID, bound.KeyID, lease.cancelIP)
				if err != nil {
					return 0, ErrInvalid
				}
				callCtx, cancel := context.WithTimeout(ctx, c.config.CallTimeout)
				job, err := c.client.Cancel(callCtx, subject, bound.RemoteJobID)
				cancel()
				if err != nil {
					return 0, err
				}
				if job.Model != bound.Model || job.TaskType != bound.TaskType {
					return 0, ErrConflict
				}
			}
			return 5 * time.Second, nil
		}
		// A close response cannot be pending. Confirm requires the current
		// business policy; revoked keys instead enter durable close recovery.
		current, err := c.store.GuardRecovery(ctx, lease)
		if err != nil || current.Terminal() {
			return 0, err
		}
		subject, err := controlclient.NewSubject(bound.Instance, bound.UserID, bound.KeyID, bound.TrustedIP)
		if err != nil {
			return 0, ErrInvalid
		}
		callCtx, cancel = context.WithTimeout(ctx, c.config.CallTimeout)
		confirmed, err := c.client.Confirm(callCtx, subject, admission.ID)
		cancel()
		if err != nil {
			*closing = *closing || closeAfterAdmissionError(err)
			return 0, err
		}
		if confirmed.ProviderRequestID != bound.ProviderRequestID || confirmed.ExternalTaskRef != bound.ID || confirmed.JobID != bound.RemoteJobID {
			return 0, ErrConflict
		}
		if _, err = c.store.BindRecoveryAdmission(ctx, lease, confirmed.ID, confirmed.JobID); err != nil {
			return 0, err
		}
		return 5 * time.Second, nil
	}
	return 0, ErrConflict
}

func (c *Coordinator) dispatch(ctx context.Context, lease RecoveryLease, closing *bool) (time.Duration, error) {
	i, request, err := c.store.Recover(ctx, lease.id)
	if err != nil {
		return 0, err
	}
	if i.Terminal() {
		return 0, nil
	}
	if _, err = c.store.MarkRecoveryDispatch(ctx, lease); err != nil {
		return 0, err
	}
	i, err = c.store.GuardRecovery(ctx, lease)
	if err != nil || i.Terminal() {
		return 0, err
	}
	subject, err := controlclient.NewSubject(i.Instance, i.UserID, i.KeyID, i.TrustedIP)
	if err != nil {
		return 0, ErrInvalid
	}
	callCtx, cancel := context.WithTimeout(ctx, c.config.CallTimeout)
	admission, err := c.client.Admit(callCtx, subject, controlclient.AdmissionRequest{ProviderRequestID: i.ProviderRequestID, ExternalTaskRef: i.ID, PriceVersion: i.PriceVersion, AuthorizationRef: i.AuthorizationRef, Request: request, Catalog: i.Catalog})
	cancel()
	if err != nil {
		*closing = *closing || closeAfterAdmissionError(err)
		return 0, err
	}
	_, err = c.store.BindRecoveryAdmission(ctx, lease, admission.ID, admission.JobID)
	return time.Second, err
}

type SettlementBatch struct {
	Seen         int
	Resolved     int
	Acknowledged int
	Conflicts    int
}

// ReconcileSettlements is independent from intent recovery leases. The source
// service queue belongs to this ledger database; unknown events are never acked.
func (c *Coordinator) ReconcileSettlements(ctx context.Context) (SettlementBatch, error) {
	ctx, stop := context.WithTimeout(ctx, time.Minute)
	defer stop()
	callCtx, cancel := context.WithTimeout(ctx, c.config.CallTimeout)
	events, err := c.client.Settlements(callCtx)
	cancel()
	if err != nil {
		return SettlementBatch{}, err
	}
	result := SettlementBatch{Seen: len(events)}
	var firstErr error
	for _, event := range events {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		settled, err := c.store.FinalizeGatewaySettlement(ctx, c.config.Target, event)
		if err != nil {
			result.Conflicts++
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		result.Resolved++
		callCtx, cancel := context.WithTimeout(ctx, c.config.CallTimeout)
		err = c.client.Acknowledge(callCtx, event.ID, settled.LedgerRef)
		cancel()
		if err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		result.Acknowledged++
	}
	return result, firstErr
}
