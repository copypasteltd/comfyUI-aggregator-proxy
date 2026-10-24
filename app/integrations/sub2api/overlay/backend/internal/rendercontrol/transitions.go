package rendercontrol

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/repository"
)

// MarkDispatch commits before any admission HTTP request. A crash after this
// commit conservatively counts as a possibly sent request, even if no send ran.
func (s *Store) MarkDispatch(ctx context.Context, id string) (Intent, error) {
	return s.markDispatch(ctx, id, nil)
}

func (s *Store) MarkRecoveryDispatch(ctx context.Context, lease RecoveryLease) (Intent, error) {
	if !lease.valid() {
		return Intent{}, ErrFence
	}
	return s.markDispatch(ctx, lease.id, &lease)
}

func (s *Store) markDispatch(ctx context.Context, id string, lease *RecoveryLease) (Intent, error) {
	if !validID("rc", id) {
		return Intent{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockMutation(ctx, tx, id, lease)
	if err != nil {
		return Intent{}, err
	}
	if !gatewayPattern.MatchString(i.TargetGatewayID) || i.Terminal() || i.State == "admitted" || i.DispatchCount >= 2147483647 {
		return Intent{}, ErrConflict
	}
	if _, err = tx.ExecContext(ctx, `UPDATE render_control_intents SET state='dispatching',dispatch_count=dispatch_count+1,updated_at=NOW() WHERE id=$1`, i.ID); err != nil {
		return Intent{}, ErrStorage
	}
	i.State, i.DispatchCount = "dispatching", i.DispatchCount+1
	return i, commit(tx)
}

func (s *Store) BindAdmission(ctx context.Context, id, admissionID, jobID string) (Intent, error) {
	return s.bindAdmission(ctx, id, admissionID, jobID, nil)
}

func (s *Store) BindRecoveryAdmission(ctx context.Context, lease RecoveryLease, admissionID, jobID string) (Intent, error) {
	if !lease.valid() {
		return Intent{}, ErrFence
	}
	return s.bindAdmission(ctx, lease.id, admissionID, jobID, &lease)
}

func (s *Store) bindAdmission(ctx context.Context, id, admissionID, jobID string, lease *RecoveryLease) (Intent, error) {
	if !validID("rc", id) || !validID("adm", admissionID) || !validID("job", jobID) {
		return Intent{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockMutation(ctx, tx, id, lease)
	if err != nil {
		return Intent{}, err
	}
	if !gatewayPattern.MatchString(i.TargetGatewayID) || i.DispatchCount == 0 || i.AdmissionID != "" && i.AdmissionID != admissionID || i.RemoteJobID != "" && i.RemoteJobID != jobID || i.State == "released" {
		return Intent{}, ErrConflict
	}
	state := "admitted"
	if i.Terminal() {
		state = i.State // A late admission response cannot undo settlement.
	}
	if _, err = tx.ExecContext(ctx, `UPDATE render_control_intents SET admission_id=$2,remote_job_id=$3,state=$4,updated_at=NOW() WHERE id=$1`, id, admissionID, jobID, state); err != nil {
		return Intent{}, ErrConflict
	}
	i.AdmissionID, i.RemoteJobID, i.State = admissionID, jobID, state
	return i, commit(tx)
}

// ReleaseUnsent is service-internal. It races atomically against MarkDispatch;
// once even one HTTP send might have happened, trusted settlement or a validated
// durable closure is required. A timeout/404/revoked Key is never enough.
func (s *Store) ReleaseUnsent(ctx context.Context, id string) (Intent, error) {
	return s.releaseUnsent(ctx, id, nil)
}

func (s *Store) ReleaseRecoveryUnsent(ctx context.Context, lease RecoveryLease) (Intent, error) {
	if !lease.valid() {
		return Intent{}, ErrFence
	}
	return s.releaseUnsent(ctx, lease.id, &lease)
}

func (s *Store) releaseUnsent(ctx context.Context, id string, lease *RecoveryLease) (Intent, error) {
	if !validID("rc", id) {
		return Intent{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockMutation(ctx, tx, id, lease)
	if err != nil {
		return Intent{}, err
	}
	if i.State == "released" && i.Outcome == "not_dispatched" {
		return i, commit(tx)
	}
	if i.DispatchCount > 0 {
		return Intent{}, ErrDispatched
	}
	if i.State != "reserved" {
		return Intent{}, ErrConflict
	}
	i.State, i.Outcome, i.TerminalHash = "released", "not_dispatched", digest([]byte("render-control-unsent-v1:"+i.ID))
	if err = settleFunds(ctx, tx, &i); err != nil {
		return Intent{}, err
	}
	return i, commit(tx)
}

// Finalize accepts a service-authenticated event from this configured gateway
// instance. It must never be exposed to a customer or called using their key.
func (s *Store) Finalize(ctx context.Context, target Target, instance string, event controlclient.Settlement) (Intent, error) {
	if !gatewayPattern.MatchString(target.gatewayID) || !instancePattern.MatchString(instance) || !validID("rc", event.ExternalTaskRef) || !validID("evt", event.ID) || !validID("job", event.JobID) || !validID("rca", event.AuthorizationRef) || !labelPattern.MatchString(event.PriceVersion) || event.ImageCount < 0 || event.VideoMilliseconds < 0 || event.AudioMilliseconds < 0 || event.AudioMilliseconds > 600_000 {
		return Intent{}, ErrInvalid
	}
	if event.Outcome != "succeeded" && event.Outcome != "failed" && event.Outcome != "cancelled" && event.Outcome != "expired" {
		return Intent{}, ErrInvalid
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return Intent{}, ErrInvalid
	}
	fingerprint := digest(encoded)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockIntent(ctx, tx, event.ExternalTaskRef)
	if err != nil {
		return Intent{}, err
	}
	if i.TargetGatewayID != target.gatewayID || i.Instance != instance || i.AuthorizationRef != event.AuthorizationRef || i.PriceVersion != event.PriceVersion || i.DispatchCount == 0 || i.RemoteJobID != "" && i.RemoteJobID != event.JobID {
		return Intent{}, ErrConflict
	}
	if i.Terminal() {
		if i.State != "settled" || i.TerminalEventID != event.ID || i.TerminalHash != fingerprint {
			return Intent{}, ErrConflict
		}
		return i, commit(tx)
	}
	actual := Money(0)
	if event.Outcome == "succeeded" {
		units := event.ImageCount
		if i.TaskType == "text_to_audio" {
			if event.ImageCount != 0 || event.VideoMilliseconds != 0 || event.AudioMilliseconds < 1 {
				return Intent{}, ErrConflict
			}
			units = (event.AudioMilliseconds + 999) / 1000
		} else if i.TaskType == "text_to_video" || i.TaskType == "references_to_video" {
			if event.AudioMilliseconds != 0 || event.ImageCount != 0 || event.VideoMilliseconds < 1 || event.VideoMilliseconds > 86_400_000 {
				return Intent{}, ErrConflict
			}
			units = (event.VideoMilliseconds + 999) / 1000
		} else if event.VideoMilliseconds != 0 || event.AudioMilliseconds != 0 {
			return Intent{}, ErrConflict
		}
		if units < 1 || units > i.MaxUnits {
			return Intent{}, ErrConflict
		}
		if actual, err = pricedAmount(i.UnitPrice, units, i.RateMultiplier); err != nil || actual > i.HoldAmount {
			return Intent{}, ErrConflict
		}
	} else if event.ImageCount != 0 || event.VideoMilliseconds != 0 || event.AudioMilliseconds != 0 {
		return Intent{}, ErrConflict
	}
	i.State, i.RemoteJobID, i.TerminalEventID, i.TerminalHash, i.Outcome, i.ActualAmount = "settled", event.JobID, event.ID, fingerprint, event.Outcome, actual
	i.ImageCount, i.VideoMilliseconds, i.AudioMilliseconds = event.ImageCount, event.VideoMilliseconds, event.AudioMilliseconds
	if err = settleFunds(ctx, tx, &i); err != nil {
		return Intent{}, err
	}
	return i, commit(tx)
}

// All monetary/counter/dedup/outbox changes share the locked intent transaction.
func settleFunds(ctx context.Context, tx *sql.Tx, i *Intent) error {
	applied, err := repository.ClaimRenderControlBilling(ctx, tx, "render-control:"+i.ID+":terminal", i.KeyID, i.TerminalHash)
	if err != nil || !applied {
		// A dedup row without the matching terminal intent is inconsistent, not a
		// successful replay. No separate refund or charge is allowed to proceed.
		return ErrConflict
	}
	if i.BillingType == 1 {
		if err = settleSubscription(ctx, tx, *i); err != nil {
			return err
		}
	} else {
		result, err := tx.ExecContext(ctx, `UPDATE users SET balance=balance+$1::numeric-$2::numeric,frozen_balance=frozen_balance-$1::numeric,updated_at=NOW() WHERE id=$3 AND frozen_balance >= $1::numeric`, i.HoldAmount.String(), i.ActualAmount.String(), i.UserID)
		if err != nil {
			return ErrStorage
		}
		if affected, err := result.RowsAffected(); err != nil || affected != 1 {
			return ErrConflict
		}
	}
	if i.ActualAmount > 0 {
		if err = repository.IncrementRenderControlKeyUsage(ctx, tx, i.KeyID, i.ActualAmount.String()); err != nil {
			return ErrStorage
		}
	}
	if i.LedgerRef, err = newID("rcl"); err != nil {
		return err
	}
	var eventID, jobID any
	if i.TerminalEventID != "" {
		eventID = i.TerminalEventID
	}
	if i.RemoteJobID != "" {
		jobID = i.RemoteJobID
	}
	if _, err = tx.ExecContext(ctx, `UPDATE render_control_intents SET state=$2,remote_job_id=$3,terminal_event_id=$4,terminal_hash=$5,outcome=$6,actual_amount=$7::numeric,ledger_ref=$8,image_count=$9,video_milliseconds=$10,audio_milliseconds=$11,updated_at=NOW() WHERE id=$1`, i.ID, i.State, jobID, eventID, i.TerminalHash, i.Outcome, i.ActualAmount.String(), i.LedgerRef, i.ImageCount, i.VideoMilliseconds, i.AudioMilliseconds); err != nil {
		return ErrConflict
	}
	return addEffect(ctx, tx, *i, "terminal")
}
