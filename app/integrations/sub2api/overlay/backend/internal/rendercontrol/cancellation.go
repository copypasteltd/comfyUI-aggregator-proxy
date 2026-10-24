package rendercontrol

import (
	"context"
	"time"
)

// RequestCancellation records an authorized customer request; it neither calls
// HTTP nor releases funds. Invalidating the old lease fences its previous plan.
func (s *Store) RequestCancellation(ctx context.Context, subject Subject, id string) (Intent, error) {
	if _, err := subject.GatewaySubject(); err != nil || !validID("rc", id) {
		return Intent{}, ErrNotFound
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	i, err := lockIntent(ctx, tx, id)
	if err != nil {
		return Intent{}, err
	}
	if i.Instance != subject.instance || i.UserID != subject.userID || i.KeyID != subject.keyID {
		return Intent{}, ErrNotFound
	}
	if i.Terminal() {
		return i, commit(tx)
	}
	var requested bool
	var fence int64
	if err = tx.QueryRowContext(ctx, `SELECT cancel_requested,fence FROM render_control_recovery WHERE intent_id=$1 FOR UPDATE`, id).Scan(&requested, &fence); err != nil {
		return Intent{}, ErrStorage
	}
	if !requested {
		if fence == int64(9223372036854775807) {
			return Intent{}, ErrConflict
		}
		if _, err = tx.ExecContext(ctx, `UPDATE render_control_recovery SET cancel_requested=TRUE,cancel_client_ip=$2::inet,close_requested=TRUE,
			lease_owner=NULL,lease_until=NULL,fence=fence+1,next_attempt_at=clock_timestamp(),updated_at=clock_timestamp() WHERE intent_id=$1`, id, subject.ip.String()); err != nil {
			return Intent{}, ErrStorage
		}
	}
	return i, commit(tx)
}
