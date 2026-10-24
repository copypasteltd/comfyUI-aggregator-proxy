package rendercontrol

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync"
	"testing"
	"time"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"
	dbent "github.com/Wei-Shaw/sub2api/ent"
	_ "github.com/Wei-Shaw/sub2api/ent/runtime"
	"github.com/Wei-Shaw/sub2api/internal/config"
	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/repository"
	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/service"
)

type publicFixture struct {
	s          *Store
	subject    Subject
	credential string
	groupID    int64
	config     PublicConfig
	auth       middleware.APIKeyAuthMiddleware
	server     *httptest.Server
	g          *recoveryGateway
	handler    *PublicHandler
}

func publicSetup(t *testing.T) *publicFixture {
	t.Helper()
	s, subject := recoveryFixture(t)
	f := &publicFixture{s: s, subject: subject}
	if err := testDB.QueryRow(`SELECT key,group_id FROM api_keys WHERE id=$1`, subject.keyID).Scan(&f.credential, &f.groupID); err != nil {
		t.Fatal("public auth fixture failed")
	}
	runSQL(t, `UPDATE groups SET allow_image_generation=TRUE WHERE id=$1`, f.groupID)
	seed := Intent{Instance: subject.instance, UserID: subject.userID, KeyID: subject.keyID, TrustedIP: subject.ip, TargetGatewayID: testTarget(t).gatewayID}
	f.g = coordinatorGateway(t, seed)
	client := dbent.NewClient(dbent.Driver(entsql.OpenDB(dialect.Postgres, testDB)))
	keyRepository := repository.NewAPIKeyRepository(client, testDB)
	// Real production auth/service/repository, standard mode; caches disabled.
	// The shared test DB is closed only by TestMain, never by this ent wrapper.
	authConfig := &config.Config{RunMode: config.RunModeStandard}
	keys := service.NewAPIKeyService(keyRepository, nil, nil, nil, nil, nil, authConfig)
	if _, err := keys.GetByKey(ctx(), f.credential); err != nil {
		t.Fatal("real upstream auth fixture failed", err)
	}
	f.auth = middleware.NewAPIKeyAuthMiddleware(keys, nil, authConfig)
	account := effectAccount(t)
	f.config = PublicConfig{Instance: subject.instance, Clients: map[string]*controlclient.Client{testTarget(t).gatewayID: f.g.client}, Models: []PublicModelRule{{UsageAccountID: account, Model: "studio-image-v1", TaskType: "text_to_image", Target: testTarget(t), PriceVersion: "prices-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"size": "1024x1024", "quality": "standard"}}}}
	f.config.UploadDirectory = hostingDirectory(t)
	f.start(t)
	return f
}

func (f *publicFixture) start(t *testing.T) {
	t.Helper()
	handler, err := NewPublicHandler(f.s, f.config)
	if err != nil {
		t.Fatal("public handler configuration invalid", err)
	}
	f.handler = handler
	router, err := handler.NewNativeRouter(f.auth, nil)
	if err != nil {
		t.Fatal(err)
	}
	f.server = httptest.NewTLSServer(router)
	f.server.Client().Timeout = 8 * time.Second
	t.Cleanup(f.server.Close)
}

func (f *publicFixture) call(t *testing.T, method, path, body string, headers map[string]string) (int, []byte, http.Header) {
	t.Helper()
	request, err := http.NewRequest(method, f.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Error("public request creation failed")
		return 0, nil, nil
	}
	request.Header.Set("Authorization", "Bearer "+f.credential)
	request.Header.Set("Content-Type", "application/json")
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	response, err := f.server.Client().Do(request)
	if err != nil {
		t.Error("public HTTPS request failed")
		return 0, nil, nil
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 3<<20))
	if err != nil {
		t.Error("public response read failed")
	}
	return response.StatusCode, data, response.Header
}

const publicImageBody = `{"model":"studio-image-v1","task_type":"text_to_image","input":{"prompt":"synthetic-private-prompt"}}`

func (f *publicFixture) submit(t *testing.T, token string) Intent {
	t.Helper()
	status, raw, headers := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": token})
	var result publicJob
	if json.Unmarshal(raw, &result) != nil || status != http.StatusAccepted || !validID("rc", result.ID) || headers.Get("Location") != "/v1/jobs/"+result.ID || headers.Get("Cache-Control") != "no-store" {
		t.Fatal("durable public submit failed", status, string(raw))
	}
	i, err := f.s.Load(ctx(), f.subject, result.ID)
	if err != nil {
		t.Fatal(err)
	}
	f.g.configure(func(g *recoveryGateway) { g.i = i })
	return i
}

func TestPublicIngressAuthenticatesAndIgnoresForgedIdentity(t *testing.T) {
	f := publicSetup(t)
	for name, credential := range map[string]string{"anonymous": "", "service_realm": "Bearer " + testTarget(t).gatewayID + "." + strings.Repeat("s", 43), "arbitrary": "Bearer synthetic-invalid"} {
		t.Run(name, func(t *testing.T) {
			status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Authorization": credential})
			if status != 401 {
				t.Fatal("invalid public authority accepted", status)
			}
		})
	}
	status, raw, headers := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "trusted-public", "X-External-Tenant-Ref": "forged:user:1", "X-External-Key-Ref": "forged:key:1", "X-Client-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.8", "X-Request-ID": "customer-chosen"})
	var result publicJob
	if status != 202 || json.Unmarshal(raw, &result) != nil || result.Status != "admission_pending" || result.CreatedAt < 1 || headers.Get("X-Request-ID") == "customer-chosen" || headers.Get("Retry-After") != "2" {
		t.Fatal("public submit projection invalid", status)
	}
	i, err := f.s.Load(ctx(), f.subject, result.ID)
	if err != nil || i.Model != "studio-image-v1" || i.TrustedIP != netip.MustParseAddr("127.0.0.1") || i.DispatchCount != 0 {
		t.Fatal("untrusted identity/IP or dispatch entered persisted intent", err)
	}
	if bytes.Contains(raw, []byte("synthetic-private-prompt")) || bytes.Contains(raw, []byte(i.AuthorizationRef)) || bytes.Contains(raw, []byte(i.ProviderRequestID)) {
		t.Fatal("public response disclosed internal request metadata")
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
	if f.g.count(admissionPath) != 0 || countRows(t, `SELECT count(*) FROM render_control_recovery WHERE intent_id=$1`, i.ID) != 1 {
		t.Fatal("HTTP submit bypassed the durable recovery queue")
	}
	status, raw, _ = f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	if status != 200 || json.Unmarshal(raw, &result) != nil || result.ID != i.ID || result.Status != "admission_pending" {
		t.Fatal("owned pending task not queryable")
	}
}

func TestPublicIngressConcurrentIdempotencyAndFrozenDefaults(t *testing.T) {
	f := publicSetup(t)
	var wg sync.WaitGroup
	ids := make(chan string, 8)
	for n := 0; n < 8; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "parallel-public"})
			var result publicJob
			if status != 202 || json.Unmarshal(raw, &result) != nil {
				t.Error("concurrent idempotency failed", status)
			}
			ids <- result.ID
		}()
	}
	wg.Wait()
	close(ids)
	set := map[string]bool{}
	for id := range ids {
		set[id] = true
	}
	if len(set) != 1 || set[""] || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 {
		t.Fatal("idempotency produced duplicate public tasks")
	}
	var original string
	for id := range set {
		original = id
	}
	status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", strings.Replace(publicImageBody, "synthetic-private-prompt", "changed", 1), map[string]string{"Idempotency-Key": "parallel-public"})
	if status != 409 {
		t.Fatal("changed public request silently reused its task")
	}
	f.config.Models[0].Defaults["size"] = "512x512"
	f.config.Models[0].UnitPrice = money(t, "2")
	f.config.Models[0].PriceVersion = "prices-v2"
	f.start(t)
	status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "parallel-public"})
	var replay publicJob
	_ = json.Unmarshal(raw, &replay)
	if status != 202 || replay.ID != original {
		t.Fatal("configuration change broke original public idempotency")
	}
	i, recovered, err := f.s.Recover(ctx(), original)
	if err != nil || i.UnitPrice != money(t, "1") || recovered.Parameters["size"] != "1024x1024" {
		t.Fatal("replay replaced frozen request or price")
	}
	created := f.submit(t, "after-price-change")
	_, next, err := f.s.Recover(ctx(), created.ID)
	if err != nil || created.UnitPrice != money(t, "2") || next.Parameters["size"] != "512x512" {
		t.Fatal("new requests did not use the new configuration")
	}
	balances(t, f.subject, "7.00000000", "3.00000000", "0.00000000")
}

func TestPublicIngressOmittedIdempotencyCreatesDistinctTasks(t *testing.T) {
	f := publicSetup(t)
	ids := map[string]bool{}
	for n := 0; n < 2; n++ {
		status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, nil)
		var result publicJob
		if status != 202 || json.Unmarshal(raw, &result) != nil {
			t.Fatal("non-idempotent submit failed", status)
		}
		ids[result.ID] = true
	}
	if len(ids) != 2 {
		t.Fatal("prompt contents became an implicit idempotency key")
	}
	balances(t, f.subject, "8.00000000", "2.00000000", "0.00000000")
}

func TestPublicIngressAcceptsFiveCapabilitiesAndConfiguredVideoDefaults(t *testing.T) {
	for _, task := range []string{"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video"} {
		t.Run(task, func(t *testing.T) {
			f := publicSetup(t)
			f.config.Models[0].TaskType = task
			video := task == "text_to_video" || task == "references_to_video"
			if video {
				f.config.Models[0].Defaults["seconds"] = json.Number("5.125")
			}
			f.start(t)
			raw, _ := json.Marshal(controlclient.JobRequest{Model: "studio-image-v1", TaskType: task, Input: configurationInput(task)})
			status, response, _ := f.call(t, http.MethodPost, "/v1/jobs", string(raw), map[string]string{"Idempotency-Key": "five-capabilities"})
			var result publicJob
			if status != 202 || json.Unmarshal(response, &result) != nil || result.TaskType != task {
				t.Fatal("supported capability failed public admission", status)
			}
			i, request, err := f.s.Recover(ctx(), result.ID)
			if err != nil {
				t.Fatal(err)
			}
			if video {
				if i.MaxUnits != 6 || request.Parameters["seconds"] != json.Number("5.125") {
					t.Fatal("configured video default was not frozen in seconds")
				}
				balances(t, f.subject, "4.00000000", "6.00000000", "0.00000000")
			} else {
				balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
			}
		})
	}
}

func TestPublicIngressScopesAndCurrentPolicy(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "scoped-public")
	other := fixture(t, "10")
	var otherCredential string
	if testDB.QueryRow(`SELECT key FROM api_keys WHERE id=$1`, other.keyID).Scan(&otherCredential) != nil {
		t.Fatal("other Key fixture failed")
	}
	for _, action := range []struct{ method, suffix string }{{http.MethodGet, ""}, {http.MethodPost, "/cancel"}} {
		status, _, _ := f.call(t, action.method, "/v1/jobs/"+i.ID+action.suffix, "", map[string]string{"Authorization": "Bearer " + otherCredential, "X-External-Key-Ref": f.subject.instance + ":key:1"})
		if status != 404 {
			t.Fatal("foreign Key accessed an owned task", status)
		}
	}
	sameUserKey, _ := newID("test")
	runSQL(t, `INSERT INTO api_keys(user_id,group_id,key,name) VALUES ($1,$2,$3,'same-user-other-key')`, f.subject.userID, f.groupID, sameUserKey)
	for _, action := range []struct{ method, suffix string }{{http.MethodGet, ""}, {http.MethodPost, "/cancel"}} {
		status, _, _ := f.call(t, action.method, "/v1/jobs/"+i.ID+action.suffix, "", map[string]string{"Authorization": "Bearer " + sameUserKey})
		if status != 404 {
			t.Fatal("same-user distinct Key crossed task isolation", status)
		}
	}
	status, _, _ := f.call(t, http.MethodGet, "/v1/jobs/job_"+i.ID[3:], "", nil)
	if status != 404 {
		t.Fatal("remote ID was accepted as a public task ID")
	}
	runSQL(t, `UPDATE groups SET model_allowlist='{"enabled":true,"models":["other-model"]}'::jsonb WHERE id=$1`, f.groupID)
	status, _, _ = f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	if status != 404 {
		t.Fatal("current group model restriction did not apply", status)
	}
	runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1`, f.subject.keyID)
	status, _, _ = f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	if status != 401 {
		t.Fatal("disabled public Key retained task access", status)
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestPublicIngressExistingTaskBillingExceptionKeepsAuthentication(t *testing.T) {
	for _, condition := range []string{"balance_zero", "key_expired", "quota_exhausted", "key_disabled", "user_disabled", "ip_denied"} {
		t.Run(condition, func(t *testing.T) {
			f := publicSetup(t)
			i := f.submit(t, "metadata-auth-exception")
			allowed := true
			switch condition {
			case "balance_zero":
				runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
			case "key_expired":
				runSQL(t, `UPDATE api_keys SET status='expired',expires_at=NOW()-INTERVAL '1 second' WHERE id=$1`, f.subject.keyID)
			case "quota_exhausted":
				runSQL(t, `UPDATE api_keys SET status='quota_exhausted',quota=1,quota_used=1 WHERE id=$1`, f.subject.keyID)
			case "key_disabled":
				runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1`, f.subject.keyID)
				allowed = false
			case "user_disabled":
				runSQL(t, `UPDATE users SET status='disabled' WHERE id=$1`, f.subject.userID)
				allowed = false
			case "ip_denied":
				runSQL(t, `UPDATE api_keys SET ip_whitelist='["192.0.2.10/32"]'::jsonb WHERE id=$1`, f.subject.keyID)
				allowed = false
			}
			status, _, _ := f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", map[string]string{"X-Forwarded-For": "192.0.2.10"})
			if allowed && status != 200 || !allowed && status != 401 && status != 403 {
				t.Fatal("metadata exception changed authentication or billing behavior", status)
			}
			status, _, _ = f.call(t, http.MethodPost, "/v1/jobs/"+i.ID+"/cancel", "", nil)
			if allowed && status != 202 || !allowed && status != 401 && status != 403 {
				t.Fatal("cancel exception changed authentication or billing behavior", status)
			}
			status, _, _ = f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, nil)
			if status == 202 {
				t.Fatal("existing-task exception allowed new generation")
			}
			if condition == "balance_zero" {
				for _, target := range []struct{ method, path string }{{http.MethodGet, "/v1/jobs/not-a-task"}, {http.MethodGet, "/v1/jobs/" + i.ID + "/extra"}, {http.MethodDelete, "/v1/jobs/" + i.ID}, {http.MethodPost, "/v1/jobs/" + i.ID + "/cancel/extra"}} {
					status, _, _ := f.call(t, target.method, target.path, "", nil)
					if status != 403 {
						t.Fatal("billing exception widened to an unrelated method or path", status)
					}
				}
			}
		})
	}
}

func TestPublicIngressRejectsCurrentQuotaBalanceAndUnsupportedBilling(t *testing.T) {
	for _, condition := range []string{"quota", "balance", "subscription", "image_permission", "group_route"} {
		t.Run(condition, func(t *testing.T) {
			f := publicSetup(t)
			expected := 403
			switch condition {
			case "quota":
				runSQL(t, `UPDATE api_keys SET quota=0.5 WHERE id=$1`, f.subject.keyID)
				expected = 429
			case "balance":
				runSQL(t, `UPDATE users SET balance=0.5 WHERE id=$1`, f.subject.userID)
			case "subscription":
				runSQL(t, `UPDATE groups SET subscription_type='subscription' WHERE id=$1`, f.groupID)
			case "image_permission":
				runSQL(t, `UPDATE groups SET allow_image_generation=FALSE WHERE id=$1`, f.groupID)
			case "group_route":
				f.config.Models[0].GroupIDs = []int64{f.groupID + 1000000}
				f.start(t)
				expected = 404
			}
			status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, nil)
			if status != expected {
				t.Fatal("current billing or model policy was ignored", status)
			}
			if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
				t.Fatal("denied admission persisted a hold")
			}
		})
	}
}

func TestPublicIngressRejectsInvalidMediaAndLimitsBeforeReserve(t *testing.T) {
	f := publicSetup(t)
	cases := []struct {
		name, body string
		headers    map[string]string
		status     int
	}{
		{"media", publicImageBody, map[string]string{"Content-Type": "text/plain"}, 415},
		{"compression", publicImageBody, map[string]string{"Content-Encoding": "gzip"}, 415},
		{"empty_idempotency", publicImageBody, map[string]string{"Idempotency-Key": ""}, 400},
		{"invalid_idempotency", publicImageBody, map[string]string{"Idempotency-Key": "contains space"}, 400},
		{"unknown_model", strings.Replace(publicImageBody, "studio-image-v1", "unknown", 1), nil, 404},
		{"wrong_capability", strings.Replace(publicImageBody, "text_to_image", "text_to_video", 1), nil, 404},
		{"unknown_field", strings.TrimSuffix(publicImageBody, "}") + `,"price":0}`, nil, 400},
		{"too_large", strings.Repeat(" ", 2<<20) + publicImageBody, nil, 413},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", item.body, item.headers)
			if status != item.status {
				t.Fatal("invalid request status mismatch", status)
			}
		})
	}
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("invalid request reserved money")
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestPublicIngressBoundQueryChecksRemoteAndKeepsPublicID(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "bound-public")
	c := testCoordinator(t, f.s, f.subject, f.g, "public-worker")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, false)
	status, raw, _ := f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", map[string]string{"X-Client-IP": "203.0.113.22"})
	var result publicJob
	if status != 200 || json.Unmarshal(raw, &result) != nil || result.ID != i.ID || result.Status != "queued" || bytes.Contains(raw, []byte("job_"+i.ID[3:])) {
		t.Fatal("public query changed ID or disclosed remote binding")
	}
	f.g.configure(func(g *recoveryGateway) { g.jobModelOverride = "foreign-model" })
	status, _, _ = f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	if status != 409 {
		t.Fatal("contradictory remote model was returned to the customer")
	}
	f.g.configure(func(g *recoveryGateway) { g.jobModelOverride = ""; g.jobDenied = true })
	status, _, _ = f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	if status != 403 {
		t.Fatal("current control-plane policy denial was ignored")
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestPublicIngressCancellationFencesAndRecovers(t *testing.T) {
	for _, stage := range []string{"reserved", "pending", "confirmed_receipt_lost"} {
		t.Run(stage, func(t *testing.T) {
			f := publicSetup(t)
			i := f.submit(t, "cancel-public")
			c := testCoordinator(t, f.s, f.subject, f.g, "cancel-worker")
			if stage != "reserved" {
				stepCoordinator(t, c, i, false)
			}
			if stage == "confirmed_receipt_lost" {
				stepCoordinator(t, c, i, false)
				f.g.configure(func(g *recoveryGateway) { g.dropAfter["/gateway/v1/jobs/job_"+i.ID[3:]+"/cancel"] = 1 })
			}
			dueRecovery(t, i.ID)
			old := recoveryLease(t, f.s, f.subject, i.ID, "old-plan")
			status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs/"+i.ID+"/cancel", "", nil)
			var result publicJob
			if status != 202 || json.Unmarshal(raw, &result) != nil || result.Status != "cancel_requested" {
				t.Fatal("cancellation was not durably accepted", status)
			}
			if _, err := f.s.GuardRecovery(ctx(), old); !errors.Is(err, ErrFence) {
				t.Fatal("old recovery plan survived cancellation")
			}
			if err := f.s.FinishRecovery(ctx(), old, "", false, 0); !errors.Is(err, ErrFence) {
				t.Fatal("old coordinator erased cancellation")
			}
			restarted := testCoordinator(t, store(t), f.subject, f.g, "restarted-cancel")
			stepCoordinator(t, restarted, i, stage == "confirmed_receipt_lost")
			if stage == "reserved" {
				if f.g.count(closePath) != 0 || f.g.count(admissionPath) != 0 {
					t.Fatal("unsent cancellation made an unnecessary remote request")
				}
			} else {
				balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
				batch, err := restarted.ReconcileSettlements(ctx())
				if err != nil || batch.Acknowledged != 1 {
					t.Fatal("cancelled task did not settle uniquely", err)
				}
			}
			balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
		})
	}
}

func TestCancellationPersistsTrustedRequestIPAndRollsBackOnFailure(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "cancel-address", 1, "1")
	old := recoveryLease(t, s, subject, i.ID, "old-address")
	current, _ := NewSubject(subject.instance, subject.userID, subject.keyID, netip.MustParseAddr("203.0.113.11"))
	runSQL(t, `CREATE FUNCTION rc_test_cancel_update_failure() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic cancellation failure'; END $$`)
	runSQL(t, `CREATE TRIGGER rc_test_cancel_update_failure BEFORE UPDATE ON render_control_recovery FOR EACH ROW EXECUTE FUNCTION rc_test_cancel_update_failure()`)
	defer runSQL(t, `DROP FUNCTION IF EXISTS rc_test_cancel_update_failure() CASCADE`)
	if _, err := s.RequestCancellation(ctx(), current, i.ID); !errors.Is(err, ErrStorage) {
		t.Fatal("failed cancellation was accepted")
	}
	if _, err := s.GuardRecovery(ctx(), old); err != nil {
		t.Fatal("failed cancellation invalidated the old lease")
	}
	runSQL(t, `DROP TRIGGER rc_test_cancel_update_failure ON render_control_recovery`)
	if _, err := s.RequestCancellation(ctx(), current, i.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.RequestCancellation(ctx(), subject, i.ID); err != nil {
		t.Fatal(err)
	}
	lease := recoveryLease(t, s, subject, i.ID, "cancel-address-worker")
	if !lease.cancelling || !lease.closing || lease.cancelIP != current.ip {
		t.Fatal("cancellation did not preserve the first authorized request IP")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestPublicModelAndClientFingerprintCannotBeRebound(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "immutable-public")
	for _, query := range []string{`UPDATE render_control_intents SET public_model='foreign' WHERE id=$1`, `UPDATE render_control_intents SET client_request_hash=repeat('a',64) WHERE id=$1`} {
		if _, err := testDB.Exec(query, i.ID); err == nil {
			t.Fatal("immutable public identity changed")
		}
	}
	loaded, err := f.s.Load(ctx(), f.subject, i.ID)
	if err != nil || loaded.Model != i.Model || loaded.ClientRequestHash != i.ClientRequestHash {
		t.Fatal("failed update changed public identity")
	}
}
