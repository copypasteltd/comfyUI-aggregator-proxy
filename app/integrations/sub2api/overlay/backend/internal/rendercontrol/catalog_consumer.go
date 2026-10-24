package rendercontrol

import (
	"context"
	"database/sql"
	"encoding/json"
	"sort"
	"sync"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/lib/pq"
)

type catalogSnapshot struct {
	models   map[string]publicRule
	owned    map[string]string
	readable map[string]publicRule
}
type catalogContextKey struct{}

func (h *PublicHandler) modelSnapshot(ctx context.Context) *catalogSnapshot {
	if snapshot, ok := ctx.Value(catalogContextKey{}).(*catalogSnapshot); ok {
		return snapshot
	}
	if snapshot := h.catalog.Load(); snapshot != nil {
		return snapshot
	}
	owned := map[string]string{}
	for alias, rule := range h.models {
		owned[alias] = rule.model.Target.gatewayID
	}
	return &catalogSnapshot{models: h.models, owned: owned, readable: h.models}
}

func (h *PublicHandler) rulesForCatalog(release controlclient.GatewayCatalogRelease) ([]PublicModelRule, error) {
	config, err := controlclient.ParseGatewayCatalog(release, release.GatewayID, h.instance)
	if err != nil || h.clients[release.GatewayID] == nil {
		return nil, ErrInvalid
	}
	rules := make([]PublicModelRule, 0, len(config.Models))
	for _, model := range config.Models {
		account, _ := controlclient.PositiveCatalogID(model.UsageAccountID)
		groups := make([]int64, 0, len(model.GroupIDs))
		for _, value := range model.GroupIDs {
			id, _ := controlclient.PositiveCatalogID(value)
			groups = append(groups, id)
		}
		price, err := ParseMoney(model.UnitPrice)
		if err != nil {
			return nil, ErrInvalid
		}
		target, _ := NewTarget(release.GatewayID)
		stamp := release.Stamp()
		rules = append(rules, PublicModelRule{UsageAccountID: account, Model: model.Model, TaskType: model.TaskType, Target: target, PriceVersion: model.PriceVersion, UnitPrice: price, GroupIDs: groups, Defaults: model.Defaults, Catalog: &stamp})
	}
	// Reuse the actual request/default validator, without touching spool files.
	_, err = NewPublicHandler(h.store, PublicConfig{Instance: h.instance, Models: rules, Clients: h.clients})
	return rules, err
}

func (h *PublicHandler) refreshCatalogs(ctx context.Context) error {
	// A repeatable-read snapshot prevents a confirmation observed without its
	// already committed ownership records; no HTTP occurs in this transaction.
	tx, err := h.store.db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		return ErrStorage
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `SELECT r.release_json FROM render_control_catalogs c JOIN render_control_catalog_releases r ON (r.instance,r.target_gateway_id,r.revision)=(c.instance,c.target_gateway_id,c.confirmed_revision) WHERE c.instance=$1 ORDER BY c.target_gateway_id LIMIT 65`, h.instance)
	if err != nil {
		return ErrStorage
	}
	releases := []controlclient.GatewayCatalogRelease{}
	for rows.Next() {
		var raw string
		var release controlclient.GatewayCatalogRelease
		if rows.Scan(&raw) != nil || json.Unmarshal([]byte(raw), &release) != nil {
			_ = rows.Close()
			return ErrStorage
		}
		releases = append(releases, release)
	}
	readErr := rows.Err()
	_ = rows.Close()
	if readErr != nil || len(releases) > 64 {
		return ErrStorage
	}
	snapshot := &catalogSnapshot{models: map[string]publicRule{}, owned: map[string]string{}, readable: map[string]publicRule{}}
	for alias, rule := range h.models {
		snapshot.models[alias] = rule
	}
	for _, release := range releases {
		rules, err := h.rulesForCatalog(release)
		if err != nil {
			return ErrStorage
		}
		validated, err := NewPublicHandler(h.store, PublicConfig{Instance: h.instance, Models: rules, Clients: h.clients})
		if err != nil {
			return err
		}
		for alias, rule := range snapshot.models {
			if rule.model.Target.gatewayID == release.GatewayID {
				delete(snapshot.models, alias)
			}
		}
		for alias, rule := range validated.models {
			if _, duplicate := snapshot.models[alias]; duplicate {
				return ErrConflict
			}
			snapshot.models[alias] = rule
		}
	}
	owners, err := tx.QueryContext(ctx, `SELECT model,target_gateway_id,COALESCE(task_type,''),group_ids FROM render_control_model_owners WHERE instance=$1 ORDER BY model LIMIT 16385`, h.instance)
	if err != nil {
		return ErrStorage
	}
	for owners.Next() {
		var alias, gateway, task string
		var groups pq.Int64Array
		if owners.Scan(&alias, &gateway, &task, &groups) != nil {
			_ = owners.Close()
			return ErrStorage
		}
		snapshot.owned[alias] = gateway
		if task != "" {
			target, _ := NewTarget(gateway)
			allowed := map[int64]bool{}
			for _, group := range groups {
				allowed[group] = true
			}
			snapshot.readable[alias] = publicRule{model: PublicModelRule{Model: alias, TaskType: task, Target: target}, groups: allowed}
		}
	}
	readErr = owners.Err()
	_ = owners.Close()
	if readErr != nil || len(snapshot.owned) > maxOwnedModels {
		return ErrStorage
	}
	for alias, rule := range snapshot.models {
		snapshot.readable[alias] = rule
	}
	if err = tx.Commit(); err != nil {
		return ErrStorage
	}
	h.catalog.Store(snapshot)
	return nil
}

// Current upstream group authority still applies to old tasks. Removing a
// generation rule does not orphan an already frozen request or its content.
func (h *PublicHandler) authorizeIntent(ctx context.Context, key *service.APIKey, i Intent) error {
	if _, err := h.ruleAt(ctx, key, i.Model, i.TaskType); err == nil {
		return nil
	}
	rule, exists := h.modelSnapshot(ctx).readable[i.Model]
	owner := h.modelSnapshot(ctx).owned[i.Model]
	if !exists || owner != i.TargetGatewayID || h.clients[owner] == nil || rule.model.TaskType != i.TaskType {
		return ErrNotFound
	}
	_, err := authorizePublicRule(key, i.Model, i.TaskType, rule, true)
	return err
}

type catalogConsumer struct {
	handler  *PublicHandler
	interval time.Duration
	stop     context.CancelFunc
	done     chan struct{}
	once     sync.Once
}

// Compatibility routes may need the original upload target before normalized
// request replay. Only an existing, customer-scoped idempotency token unlocks
// this frozen rule; Reserve still compares the full original client hash.
func (h *PublicHandler) requestRule(c *gin.Context, key *service.APIKey, model, task string) (publicRule, error) {
	if rule, err := h.ruleAt(c.Request.Context(), key, model, task); err == nil {
		return rule, nil
	}
	values := c.Request.Header.Values("Idempotency-Key")
	if len(values) != 1 || !asciiPublic(values[0], 1, 128) {
		return publicRule{}, ErrNotFound
	}
	subject, _, err := h.identity(c)
	if err != nil {
		return publicRule{}, err
	}
	i, err := h.store.replayCatalogRequest(c.Request.Context(), subject, publicOperation(task), values[0], "")
	if err != nil || i.Model != model || i.TaskType != task {
		return publicRule{}, ErrNotFound
	}
	if err = h.authorizeIntent(c.Request.Context(), key, i); err != nil {
		return publicRule{}, err
	}
	_, request, err := h.store.Recover(c.Request.Context(), i.ID)
	if err != nil {
		return publicRule{}, err
	}
	target, err := NewTarget(i.TargetGatewayID)
	if err != nil {
		return publicRule{}, err
	}
	return publicRule{model: PublicModelRule{UsageAccountID: i.UsageAccountID, Model: i.Model, TaskType: i.TaskType, Target: target, PriceVersion: i.PriceVersion, UnitPrice: i.UnitPrice, Defaults: request.Parameters, Catalog: i.Catalog}, groups: map[int64]bool{i.BillingGroupID: true}}, nil
}

func (c *catalogConsumer) step(ctx context.Context, gateway string) error {
	h := c.handler
	client := h.clients[gateway]
	state, err := client.GatewayCatalog(ctx, h.instance)
	if err != nil {
		return err
	}
	// A previous process may have lost the receipt or local confirmation commit.
	if state.Confirmed != nil {
		rules, err := h.rulesForCatalog(*state.Confirmed)
		if err != nil {
			return err
		}
		if code := h.persistCatalog(ctx, *state.Confirmed, rules, false); code != "" {
			return ErrStorage
		}
		if err = h.confirmCatalog(ctx, *state.Confirmed); err != nil {
			return err
		}
	}
	if state.Desired == nil || state.Confirmed != nil && state.Desired.Stamp() == state.Confirmed.Stamp() {
		return h.refreshCatalogs(ctx)
	}
	release := *state.Desired
	rules, parseErr := h.rulesForCatalog(release)
	code := ""
	if parseErr != nil || state.DesiredStale {
		code = "unsupported_configuration"
	} else {
		code = h.persistCatalog(ctx, release, rules, true)
	}
	// Persist ownership before a possible response loss; names never fall back
	// to a stock supplier even while the candidate is awaiting confirmation.
	if err = h.refreshCatalogs(ctx); err != nil {
		return err
	}
	receipt := controlclient.GatewayCatalogReceipt{InstanceID: h.instance, Revision: release.Revision, Digest: release.Digest, Status: "applied"}
	if code != "" {
		receipt.Status, receipt.ErrorCode = "rejected", &code
	}
	result, err := client.ReceiveGatewayCatalog(ctx, receipt)
	if err != nil {
		return err
	}
	if result.Activated {
		if err = h.confirmCatalog(ctx, release); err != nil {
			return err
		}
	}
	return h.refreshCatalogs(ctx)
}

func startCatalogConsumer(h *PublicHandler, interval time.Duration) (*catalogConsumer, error) {
	if interval == 0 {
		interval = 5 * time.Second
	}
	if interval < time.Second || interval > time.Minute {
		return nil, ErrInvalid
	}
	ctx, stop := context.WithCancel(context.Background())
	c := &catalogConsumer{handler: h, interval: interval, stop: stop, done: make(chan struct{})}
	gateways := make([]string, 0, len(h.clients))
	for gateway := range h.clients {
		gateways = append(gateways, gateway)
	}
	sort.Strings(gateways)
	go func() {
		defer close(c.done)
		for next := 0; ctx.Err() == nil; next = (next + 1) % len(gateways) {
			call, cancel := context.WithTimeout(ctx, 20*time.Second)
			_ = c.step(call, gateways[next])
			cancel()
			if !runtimeWait(ctx, c.interval) {
				return
			}
		}
	}()
	return c, nil
}

func (c *catalogConsumer) Stop(ctx context.Context) error {
	c.once.Do(c.stop)
	select {
	case <-c.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
