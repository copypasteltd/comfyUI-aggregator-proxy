package rendercontrol

import (
	"context"
	"database/sql"
	"net/netip"
	"time"
)

// RecoveryLease is an internal capability. Metadata handed to a public caller
// cannot construct a lease or authorize any current customer operation.
type RecoveryLease struct {
	id         string
	owner      string
	fence      int64
	target     string
	instance   string
	failures   int
	closing    bool
	cancelling bool
	cancelIP   netip.Addr
}

func (l RecoveryLease) IntentID() string { return l.id }
func (RecoveryLease) String() string     { return "rendercontrol.RecoveryLease{redacted}" }
func (RecoveryLease) GoString() string   { return "rendercontrol.RecoveryLease{redacted}" }

func (l RecoveryLease) valid() bool {
	return validID("rc", l.id) && labelPattern.MatchString(l.owner) && l.fence > 0 && gatewayPattern.MatchString(l.target) && instancePattern.MatchString(l.instance)
}

func RecoveryBackoff(failures int) time.Duration {
	if failures < 0 {
		failures = 0
	}
	if failures >= 8 {
		return 5 * time.Minute
	}
	return (2 * time.Second) << failures
}

func (s *Store) ClaimRecovery(ctx context.Context, target Target, instance, owner string, limit int, lease time.Duration) ([]RecoveryLease, error) {
	if !gatewayPattern.MatchString(target.gatewayID) || !instancePattern.MatchString(instance) || !labelPattern.MatchString(owner) || limit < 1 || limit > 100 || lease < 10*time.Second || lease > 5*time.Minute {
		return nil, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `WITH candidates AS (
		SELECT r.intent_id FROM render_control_recovery r JOIN render_control_intents i ON i.id=r.intent_id
		WHERE i.target_gateway_id=$1 AND i.instance=$2 AND i.state IN ('reserved','dispatching','admitted')
		AND r.next_attempt_at<=clock_timestamp() AND (r.lease_until IS NULL OR r.lease_until<=clock_timestamp())
		AND r.fence<9223372036854775807 ORDER BY r.next_attempt_at,r.intent_id LIMIT $3 FOR UPDATE OF r SKIP LOCKED)
		UPDATE render_control_recovery r SET lease_owner=$4,lease_until=clock_timestamp()+($5::bigint*INTERVAL '1 millisecond'),fence=r.fence+1,updated_at=clock_timestamp()
		FROM candidates c WHERE r.intent_id=c.intent_id RETURNING r.intent_id,r.fence,r.failures,r.close_requested,r.cancel_requested,COALESCE(host(r.cancel_client_ip),'')`, target.gatewayID, instance, limit, owner, lease.Milliseconds())
	if err != nil {
		return nil, ErrStorage
	}
	result := make([]RecoveryLease, 0, limit)
	for rows.Next() {
		l := RecoveryLease{owner: owner, target: target.gatewayID, instance: instance}
		var cancelIP string
		if err = rows.Scan(&l.id, &l.fence, &l.failures, &l.closing, &l.cancelling, &cancelIP); err != nil {
			_ = rows.Close()
			return nil, ErrStorage
		}
		if l.cancelling {
			if l.cancelIP, err = netip.ParseAddr(cancelIP); err != nil {
				_ = rows.Close()
				return nil, ErrStorage
			}
		}
		result = append(result, l)
	}
	readErr := rows.Err()
	_ = rows.Close()
	if readErr != nil {
		return nil, ErrStorage
	}
	return result, commit(tx)
}

// Ownership locks precede the recovery row. ClaimRecovery never locks owners or
// intents, so no reverse dependency is introduced by lease reclamation.
func lockMutation(ctx context.Context, tx *sql.Tx, id string, lease *RecoveryLease) (Intent, error) {
	i, err := lockIntent(ctx, tx, id)
	if err != nil || lease == nil {
		return i, err
	}
	if !lease.valid() || lease.id != i.ID || lease.target != i.TargetGatewayID || lease.instance != i.Instance {
		return Intent{}, ErrFence
	}
	var current bool
	err = tx.QueryRowContext(ctx, `SELECT COALESCE(lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp(),FALSE) FROM render_control_recovery WHERE intent_id=$1 FOR UPDATE`, id, lease.owner, lease.fence).Scan(&current)
	if err == sql.ErrNoRows || err == nil && !current {
		return Intent{}, ErrFence
	}
	if err != nil {
		return Intent{}, ErrStorage
	}
	return i, nil
}

// GuardRecovery rechecks current ownership before network work, then commits
// and releases every database lock before returning to the HTTP caller.
func (s *Store) GuardRecovery(ctx context.Context, lease RecoveryLease) (Intent, error) {
	if !lease.valid() {
		return Intent{}, ErrFence
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockMutation(ctx, tx, lease.id, &lease)
	if err != nil {
		return Intent{}, err
	}
	return i, commit(tx)
}

// FinishRecovery only schedules the next bounded step. Terminal intent rows
// remain as evidence but are excluded from future claims.
func (s *Store) FinishRecovery(ctx context.Context, lease RecoveryLease, code string, closing bool, delay time.Duration) error {
	if !lease.valid() || code != "" && !recoveryCodePattern.MatchString(code) || delay < 0 || delay > 5*time.Minute {
		return ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	result, err := s.db.ExecContext(ctx, `UPDATE render_control_recovery SET lease_owner=NULL,lease_until=NULL,
		next_attempt_at=clock_timestamp()+($4::bigint*INTERVAL '1 millisecond'),failures=CASE WHEN $5='' THEN 0 ELSE LEAST(failures+1,31) END,
		last_error_code=$5,close_requested=$6,updated_at=clock_timestamp()
		WHERE intent_id=$1 AND lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp()`, lease.id, lease.owner, lease.fence, delay.Milliseconds(), code, closing)
	if err != nil {
		return ErrUncertain
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return ErrFence
	}
	return nil
}
