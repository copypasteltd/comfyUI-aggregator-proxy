package rendercontrol

import (
	"context"
	"encoding/json"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

// ReleaseClosed consumes a verified remote capability under the original
// immutable target. It never performs HTTP, invents a settlement or retries a
// lost commit. Proof, funds, dedup and terminal effect commit together.
func (s *Store) ReleaseClosed(ctx context.Context, id string, proof controlclient.ClosureProof) (Intent, error) {
	return s.releaseClosed(ctx, id, proof, nil)
}

func (s *Store) ReleaseRecoveryClosed(ctx context.Context, lease RecoveryLease, proof controlclient.ClosureProof) (Intent, error) {
	if !lease.valid() {
		return Intent{}, ErrFence
	}
	return s.releaseClosed(ctx, lease.id, proof, &lease)
}

func (s *Store) releaseClosed(ctx context.Context, id string, proof controlclient.ClosureProof, lease *RecoveryLease) (Intent, error) {
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
	identity, err := i.RequestIdentity()
	if err != nil || !proof.Matches(identity) {
		return Intent{}, ErrConflict
	}
	record := struct {
		controlclient.RequestIdentity
		GatewayID string `json:"gateway_id"`
		ClosureID string `json:"closure_id"`
	}{identity, i.TargetGatewayID, proof.ID()}
	encoded, err := json.Marshal(record)
	if err != nil {
		return Intent{}, ErrInvalid
	}
	fingerprint := digest(encoded)
	if i.Terminal() {
		var storedHash string
		if i.State != "released" || i.Outcome != "remote_closed" || i.TerminalHash != fingerprint || tx.QueryRowContext(ctx, `SELECT proof_hash FROM render_control_closures WHERE intent_id=$1`, id).Scan(&storedHash) != nil || storedHash != fingerprint {
			return Intent{}, ErrConflict
		}
		return i, commit(tx)
	}
	if (i.State != "reserved" && i.State != "dispatching") || i.AdmissionID != "" || i.RemoteJobID != "" {
		return Intent{}, ErrConflict
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_closures(intent_id,target_gateway_id,closure_id,provider_request_id,external_task_ref,price_version,authorization_ref,external_tenant_ref,external_key_ref,proof_hash)
		VALUES ($1,$2,$3,$4,$1,$5,$6,$7,$8,$9)`, id, i.TargetGatewayID, proof.ID(), identity.ProviderRequestID, identity.PriceVersion, identity.AuthorizationRef, identity.ExternalTenantRef, identity.ExternalKeyRef, fingerprint); err != nil {
		return Intent{}, ErrConflict
	}
	i.State, i.Outcome, i.TerminalHash = "released", "remote_closed", fingerprint
	if err = settleFunds(ctx, tx, &i); err != nil {
		return Intent{}, err
	}
	return i, commit(tx)
}
