package rendercontrol

import (
	"context"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type RuntimeConfig struct {
	Instance           string
	Owner              string
	Workers            int
	PollInterval       time.Duration
	SettlementInterval time.Duration
	Lease              time.Duration
	CallTimeout        time.Duration
	Effects            *EffectConsumer
}

type RuntimeSnapshot struct {
	State                string
	RecoveryAttempts     uint64
	RecoveryWorked       uint64
	RecoveryFailures     uint64
	SettlementAttempts   uint64
	SettlementFailures   uint64
	SettlementsSeen      uint64
	SettlementsConfirmed uint64
	EffectAttempts       uint64
	EffectWorked         uint64
	EffectFailures       uint64
}

// Runtime schedules database claims, never an in-memory queue of tasks. It
// borrows the Store and clients; their owner closes them after Stop returns.
type Runtime struct {
	mu             sync.Mutex
	state          string
	stop           context.CancelFunc
	done           chan struct{}
	config         RuntimeConfig
	workers        [][]*Coordinator
	recoveries     atomic.Uint64
	worked         atomic.Uint64
	failures       atomic.Uint64
	settlements    atomic.Uint64
	settleFailed   atomic.Uint64
	seen           atomic.Uint64
	acked          atomic.Uint64
	effectAttempts atomic.Uint64
	effectWorked   atomic.Uint64
	effectFailures atomic.Uint64
}

func (*Runtime) String() string   { return "rendercontrol.Runtime{redacted}" }
func (*Runtime) GoString() string { return "rendercontrol.Runtime{redacted}" }

func NewRuntime(store *Store, clients map[string]*controlclient.Client, config RuntimeConfig) (*Runtime, error) {
	if config.Workers == 0 {
		config.Workers = 2
	}
	if config.PollInterval == 0 {
		config.PollInterval = time.Second
	}
	if config.SettlementInterval == 0 {
		config.SettlementInterval = 2 * time.Second
	}
	if len(clients) < 1 || len(clients) > 8 || config.Workers < 1 || config.Workers > 16 || !labelPattern.MatchString(config.Owner) || len(config.Owner) > 48 || config.PollInterval < 10*time.Millisecond || config.PollInterval > time.Minute || config.SettlementInterval < 10*time.Millisecond || config.SettlementInterval > time.Minute {
		return nil, ErrInvalid
	}
	ids := make([]string, 0, len(clients))
	for id := range clients {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	r := &Runtime{state: "new", done: make(chan struct{}), config: config}
	for worker := 0; worker < config.Workers; worker++ {
		group := make([]*Coordinator, 0, len(ids))
		for _, id := range ids {
			target, err := NewTarget(id)
			if err != nil {
				return nil, err
			}
			owner := config.Owner + "_w" + string([]byte{'a' + byte(worker)})
			coordinator, err := NewCoordinator(store, clients[id], CoordinatorConfig{Target: target, Instance: config.Instance, Owner: owner, Lease: config.Lease, CallTimeout: config.CallTimeout})
			if err != nil {
				return nil, err
			}
			group = append(group, coordinator)
		}
		r.workers = append(r.workers, group)
	}
	return r, nil
}

func (r *Runtime) Start(parent context.Context) error {
	if parent == nil || parent.Err() != nil {
		return ErrInvalid
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.state != "new" {
		return ErrConflict
	}
	ctx, cancel := context.WithCancel(parent)
	r.stop, r.state = cancel, "running"
	var group sync.WaitGroup
	group.Add(len(r.workers) + len(r.workers[0]))
	if r.config.Effects != nil {
		group.Add(1)
		go func() {
			defer group.Done()
			for ctx.Err() == nil {
				r.effectAttempts.Add(1)
				worked, err := r.config.Effects.Step(ctx)
				if worked {
					r.effectWorked.Add(1)
				}
				if err != nil && ctx.Err() == nil {
					r.effectFailures.Add(1)
				}
				if !runtimeWait(ctx, r.config.PollInterval) {
					return
				}
			}
		}()
	}
	for index, coordinators := range r.workers {
		go func(offset int, cs []*Coordinator) {
			defer group.Done()
			for next := offset; ctx.Err() == nil; next++ {
				// Reduce the cursor every iteration so even long-lived processes
				// cannot overflow an integer or accumulate a local task backlog.
				next %= len(cs)
				r.recoveries.Add(1)
				worked, err := cs[next].Step(ctx)
				if worked {
					r.worked.Add(1)
				}
				if err != nil && ctx.Err() == nil {
					r.failures.Add(1)
				}
				if !runtimeWait(ctx, r.config.PollInterval) {
					return
				}
			}
		}(index%len(coordinators), coordinators)
	}
	for _, coordinator := range r.workers[0] {
		go func(c *Coordinator) {
			defer group.Done()
			for ctx.Err() == nil {
				r.settlements.Add(1)
				batch, err := c.ReconcileSettlements(ctx)
				r.seen.Add(uint64(batch.Seen))
				r.acked.Add(uint64(batch.Acknowledged))
				if err != nil && ctx.Err() == nil {
					r.settleFailed.Add(1)
				}
				if !runtimeWait(ctx, r.config.SettlementInterval) {
					return
				}
			}
		}(coordinator)
	}
	go func() {
		group.Wait()
		cancel()
		r.mu.Lock()
		r.state = "stopped"
		close(r.done)
		r.mu.Unlock()
	}()
	return nil
}

func runtimeWait(ctx context.Context, interval time.Duration) bool {
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return ctx.Err() == nil
	}
}

// Stop is idempotent, including before Start. It never clears a database lease
// or releases money: interrupted work remains recoverable with its original ID.
func (r *Runtime) Stop(ctx context.Context) error {
	if ctx == nil {
		return ErrInvalid
	}
	r.mu.Lock()
	switch r.state {
	case "new":
		r.state = "stopped"
		close(r.done)
	case "running":
		r.state = "stopping"
		r.stop()
	}
	done := r.done
	r.mu.Unlock()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (r *Runtime) Snapshot() RuntimeSnapshot {
	r.mu.Lock()
	state := r.state
	r.mu.Unlock()
	return RuntimeSnapshot{State: state, RecoveryAttempts: r.recoveries.Load(), RecoveryWorked: r.worked.Load(), RecoveryFailures: r.failures.Load(), SettlementAttempts: r.settlements.Load(), SettlementFailures: r.settleFailed.Load(), SettlementsSeen: r.seen.Load(), SettlementsConfirmed: r.acked.Load(), EffectAttempts: r.effectAttempts.Load(), EffectWorked: r.effectWorked.Load(), EffectFailures: r.effectFailures.Load()}
}
