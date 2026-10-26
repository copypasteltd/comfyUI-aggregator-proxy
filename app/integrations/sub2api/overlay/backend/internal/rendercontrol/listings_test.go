package rendercontrol

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

// This HTTPS peer models current authorization, independently of the local
// route index. Revoking a caller changes both collection and direct reads.
type collectionGateway struct {
	mu       sync.Mutex
	t        *testing.T
	target   string
	owner    Subject
	client   *controlclient.Client
	keys     map[string]bool
	scopes   []string
	items    map[string][]controlclient.ListedContent
	failure  int
	calls    map[string]int
	block    bool
	entered  chan struct{}
	finished chan struct{}
}

func collectionPeer(t *testing.T, f *publicFixture) *collectionGateway {
	t.Helper()
	current, _ := f.subject.GatewaySubject()
	g := &collectionGateway{t: t, target: testTarget(t).gatewayID, owner: f.subject, keys: map[string]bool{current.KeyRef(): true}, scopes: []string{"jobs:read", "content:read", "content:download", "content:delete"}, items: map[string][]controlclient.ListedContent{}, calls: map[string]int{}, entered: make(chan struct{}, 1), finished: make(chan struct{}, 1)}
	server := httptest.NewTLSServer(http.HandlerFunc(g.serve))
	t.Cleanup(server.Close)
	credential, _ := controlclient.ParseCredential(g.target + "." + strings.Repeat("s", 43))
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	var err error
	g.client, err = controlclient.New(controlclient.Config{GatewayID: g.target, Origin: server.URL, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(g.client.Close)
	f.config.Clients[g.target] = g.client
	f.start(t)
	return g
}

func (g *collectionGateway) change(update func(*collectionGateway)) {
	g.mu.Lock()
	defer g.mu.Unlock()
	update(g)
}

func (g *collectionGateway) hits(path string) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.calls[path]
}

func (g *collectionGateway) serve(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.calls[r.URL.Path]++
	current, _ := g.owner.GatewaySubject()
	if r.Header.Get("Authorization") != "Bearer "+g.target+"."+strings.Repeat("s", 43) || r.Header.Get("X-External-Tenant-Ref") != current.TenantRef() || r.Header.Get("X-Client-IP") != "127.0.0.1" {
		g.t.Error("collection or shared read changed trusted identity")
	}
	key := r.Header.Get("X-External-Key-Ref")
	if !g.keys[key] {
		status := 403
		if strings.HasPrefix(r.URL.Path, "/gateway/v1/content/") {
			status = 404
		}
		gatewayTestError(w, status)
		return
	}
	if r.URL.Path == "/gateway/v1/access-policy" {
		_ = json.NewEncoder(w).Encode(controlclient.DeliveryPolicy{Scopes: g.scopes})
		return
	}
	if r.URL.Path == "/gateway/v1/content" {
		if g.block {
			g.entered <- struct{}{}
			g.mu.Unlock()
			<-r.Context().Done()
			g.finished <- struct{}{}
			g.mu.Lock()
			return
		}
		if g.failure != 0 {
			gatewayTestError(w, g.failure)
			return
		}
		if !(controlclient.DeliveryPolicy{Scopes: g.scopes}).Allows("content:read") {
			gatewayTestError(w, 403)
			return
		}
		limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
		if err != nil || limit < 1 || limit > 100 || r.Method != http.MethodGet {
			g.t.Error("invalid collection query reached remote gateway")
		}
		offset := 0
		if cursor := r.URL.Query().Get("cursor"); cursor != "" {
			offset, err = strconv.Atoi(strings.TrimPrefix(cursor, "remote-page-"))
			if err != nil {
				gatewayTestError(w, 400)
				return
			}
		}
		items := g.items[r.URL.Query().Get("model")]
		if offset < 0 || offset > len(items) {
			gatewayTestError(w, 400)
			return
		}
		end := min(offset+limit, len(items))
		data := append([]controlclient.ListedContent{}, items[offset:end]...)
		var next *string
		if end < len(items) {
			value := "remote-page-" + strconv.Itoa(end)
			next = &value
		}
		_ = json.NewEncoder(w).Encode(controlclient.CursorPage[controlclient.ListedContent]{Object: "list", Data: data, HasMore: next != nil, NextCursor: next})
		return
	}
	for _, items := range g.items {
		for _, item := range items {
			path := "/gateway/v1/content/" + item.ID
			if r.URL.Path != path && r.URL.Path != path+"/download" {
				continue
			}
			scope := "content:read"
			if strings.HasSuffix(r.URL.Path, "/download") {
				scope = "content:download"
			}
			if !(controlclient.DeliveryPolicy{Scopes: g.scopes}).Allows(scope) || r.Method == http.MethodDelete && key != current.KeyRef() {
				gatewayTestError(w, 404)
				return
			}
			if r.Method == http.MethodDelete {
				w.WriteHeader(202)
				_ = json.NewEncoder(w).Encode(controlclient.ContentDeletion{ID: item.ID, Status: "delete_requested"})
				return
			}
			if r.URL.Path == path {
				_ = json.NewEncoder(w).Encode(item.ContentMetadata)
				return
			}
			data := fixtureImage
			w.Header().Set("ETag", `"`+item.SHA256+`"`)
			w.Header().Set("Content-Type", item.MediaType)
			w.Header().Set("Content-Length", strconv.Itoa(len(data)))
			if r.Header.Get("Range") == "bytes=1-3" {
				w.Header().Set("Content-Range", "bytes 1-3/"+strconv.Itoa(len(data)))
				w.Header().Set("Content-Length", "3")
				w.WriteHeader(206)
				data = data[1:4]
			}
			if r.Method != http.MethodHead {
				_, _ = w.Write(data)
			}
			return
		}
	}
	gatewayTestError(w, 404)
}

func collectionItem(t *testing.T, model string) controlclient.ListedContent {
	t.Helper()
	id, _ := newID("content")
	job, _ := newID("job")
	ref, _ := newID("rc")
	sum := sha256.Sum256(fixtureImage)
	return controlclient.ListedContent{ContentMetadata: controlclient.ContentMetadata{ID: id, Bytes: int64(len(fixtureImage)), MediaType: "image/png", SHA256: hex.EncodeToString(sum[:]), ExpiresAt: time.Now().Add(time.Hour).Unix()}, JobID: job, CreatedAt: time.Now().Add(-time.Minute).Unix(), ExternalTaskRef: ref, Model: model, TaskType: "text_to_image"}
}

func collectionPage[T any](t *testing.T, status int, raw []byte) controlclient.CursorPage[T] {
	t.Helper()
	var page controlclient.CursorPage[T]
	if status != 200 || json.Unmarshal(raw, &page) != nil || page.Object != "list" || page.Data == nil || page.HasMore != (page.NextCursor != nil) {
		t.Fatal("invalid authorized collection response", status, string(raw))
	}
	return page
}

func sameUserKey(t *testing.T, f *publicFixture) (Subject, string) {
	t.Helper()
	credential, _ := newID("test")
	var keyID int64
	if testDB.QueryRow(`INSERT INTO api_keys(user_id,group_id,key,name) VALUES($1,$2,$3,'collection reader') RETURNING id`, f.subject.userID, f.groupID, credential).Scan(&keyID) != nil {
		t.Fatal("shared reader fixture failed")
	}
	subject, _ := NewSubject(f.subject.instance, f.subject.userID, keyID, f.subject.ip)
	return subject, credential
}

func TestCollectionJobsCursorIsStableBoundedAndOwned(t *testing.T) {
	f := publicSetup(t)
	collectionPeer(t, f)
	ids := []string{}
	for n := range 5 {
		i := reserve(t, f.s, f.subject, fmt.Sprintf("listed-%d", n), 1, "0.01")
		ids = append(ids, i.ID)
	}
	runSQL(t, `UPDATE render_control_intents SET created_at='2026-01-01T00:00:00Z' WHERE instance=$1`, f.subject.instance)
	sort.Sort(sort.Reverse(sort.StringSlice(ids)))
	status, raw, headers := f.call(t, "GET", "/v1/jobs?limit=2", "", nil)
	page := collectionPage[publicJob](t, status, raw)
	if len(page.Data) != 2 || page.Data[0].ID != ids[0] || page.Data[1].ID != ids[1] || !page.HasMore || headers.Get("Cache-Control") != "private, no-store" || bytes.Contains(raw, []byte("synthetic test prompt")) {
		t.Fatal("first cursor page is unstable or disclosed input")
	}
	newer := reserve(t, f.s, f.subject, "insert-after-first-page", 1, "0.01")
	seen := []string{page.Data[0].ID, page.Data[1].ID}
	for page.NextCursor != nil {
		status, raw, _ = f.call(t, "GET", "/v1/jobs?limit=2&cursor="+url.QueryEscape(*page.NextCursor), "", nil)
		page = collectionPage[publicJob](t, status, raw)
		for _, item := range page.Data {
			seen = append(seen, item.ID)
			if item.ID == newer.ID || item.Status != "admission_pending" {
				t.Fatal("cursor cutoff or pending intent projection changed")
			}
		}
	}
	if strings.Join(ids, ",") != strings.Join(seen, ",") {
		t.Fatal("equal timestamp pagination skipped or duplicated an intent")
	}
	_, otherKey := sameUserKey(t, f)
	status, raw, _ = f.call(t, "GET", "/v1/jobs", "", map[string]string{"Authorization": "Bearer " + otherKey})
	if page := collectionPage[publicJob](t, status, raw); len(page.Data) != 0 {
		t.Fatal("same user different Key inherited owned intents")
	}
	balances(t, f.subject, "9.94000000", "0.06000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_effects WHERE api_key_id=$1`, f.subject.keyID) != 6 {
		t.Fatal("collection generated financial side effects")
	}
}

func TestCollectionJobsStatusScanBudgetReturnsContinuation(t *testing.T) {
	f := publicSetup(t)
	collectionPeer(t, f)
	for n := range 105 {
		reserve(t, f.s, f.subject, fmt.Sprintf("scan-%d", n), 1, "0.001")
	}
	status, raw, _ := f.call(t, "GET", "/v1/jobs?limit=1&status=queued", "", nil)
	page := collectionPage[publicJob](t, status, raw)
	if len(page.Data) != 0 || !page.HasMore {
		t.Fatal("bounded status scan falsely claimed a complete result")
	}
	status, raw, _ = f.call(t, "GET", "/v1/jobs?limit=1&status=queued&cursor="+url.QueryEscape(*page.NextCursor), "", nil)
	page = collectionPage[publicJob](t, status, raw)
	if len(page.Data) != 0 || page.HasMore {
		t.Fatal("status continuation did not finish remaining candidates")
	}
	balances(t, f.subject, "9.89500000", "0.10500000", "0.00000000")
}

func TestCollectionJobsUseCurrentRemotePhaseAndOriginalTarget(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "list-bound-original")
	completeCompatibility(t, f, i, false)
	f.g.configure(func(g *recoveryGateway) { g.outcome = ""; g.jobArtifacts = nil })
	status, raw, _ := f.call(t, "GET", "/v1/jobs?status=queued", "", nil)
	queued := collectionPage[publicJob](t, status, raw)
	if len(queued.Data) != 1 || queued.Data[0].Status != "queued" || !bytes.Contains(raw, []byte(`"artifacts":[]`)) {
		t.Fatal("bound unfinished job did not preserve the empty artifact array")
	}
	f.g.configure(func(g *recoveryGateway) {
		g.outcome = "succeeded"
		g.jobArtifacts = []controlclient.Artifact{{ID: "content_" + i.ID[3:]}}
	})
	secondTarget, _ := NewTarget("gateway_" + strings.Repeat("e", 32))
	seed := Intent{Instance: f.subject.instance, UserID: f.subject.userID, KeyID: f.subject.keyID, TrustedIP: f.subject.ip, TargetGatewayID: secondTarget.gatewayID}
	second := coordinatorGateway(t, seed)
	f.config.Models[0].Target = secondTarget
	f.config.Clients[secondTarget.gatewayID] = second.client
	f.start(t)
	status, raw, _ = f.call(t, "GET", "/v1/jobs?status=succeeded", "", nil)
	page := collectionPage[publicJob](t, status, raw)
	if len(page.Data) != 1 || page.Data[0].ID != i.ID || page.Data[0].Status != "succeeded" || len(page.Data[0].Artifacts) != 1 || second.count("/gateway/v1/jobs/job_"+i.ID[3:]) != 0 {
		t.Fatal("list used stale database phase or changed original gateway")
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = []string{"content:read"} })
	status, raw, _ = f.call(t, "GET", "/v1/jobs", "", nil)
	if page := collectionPage[publicJob](t, status, raw); len(page.Data) != 0 {
		t.Fatal("revoked original target jobs scope remained visible")
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestCollectionCursorsBindSubjectScopeAndFixedExpiry(t *testing.T) {
	s, subject := recoveryFixture(t)
	now := time.Now().UTC()
	position, err := s.readListingCursor(subject, "content", "scope", "", now)
	if err != nil {
		t.Fatal(err)
	}
	position.Remote = strings.Repeat("a", 4096)
	token, err := s.writeListingCursor(subject, "content", "scope", position)
	if err != nil || len(*token) > 8192 || strings.Contains(*token, subject.instance) || strings.Contains(*token, strings.Repeat("a", 30)) {
		t.Fatal("cursor exposed plaintext or could not wrap a full remote cursor")
	}
	position, err = s.readListingCursor(subject, "content", "scope", *token, now.Add(800*time.Second))
	if err != nil {
		t.Fatal("valid fixed lifetime cursor rejected", err)
	}
	rotated, err := NewStore(testDB, "new-key", map[string][]byte{"test-key-v1": bytes.Repeat([]byte{7}, 32), "new-key": bytes.Repeat([]byte{8}, 32)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := rotated.readListingCursor(subject, "content", "scope", *token, now); err != nil {
		t.Fatal("retained cursor encryption key broke rotation")
	}
	continued, _ := rotated.writeListingCursor(subject, "content", "scope", position)
	if _, err := rotated.readListingCursor(subject, "content", "scope", *continued, now.Add(901*time.Second)); !errors.Is(err, errCursorExpired) {
		t.Fatal("continuation extended the original expiry")
	}
	for _, change := range []func(*Subject, *string, *string, *string){
		func(s *Subject, p, scope, token *string) { s.keyID++ },
		func(s *Subject, p, scope, token *string) { s.userID++ },
		func(s *Subject, p, scope, token *string) { s.instance = "other-instance" },
		func(s *Subject, p, scope, token *string) { *p = "jobs" },
		func(s *Subject, p, scope, token *string) { *scope = "changed-limit-or-policy" },
		func(s *Subject, p, scope, token *string) { *token = strings.Replace(*token, ".", ".a", 1) },
	} {
		other, purpose, scope, changed := subject, "content", "scope", *token
		change(&other, &purpose, &scope, &changed)
		if _, err := s.readListingCursor(other, purpose, scope, changed, now); !errors.Is(err, errCursorInvalid) {
			t.Fatal("authenticated cursor crossed its subject or query scope")
		}
	}
}

func TestContentCollectionRequiresFreshExplicitSharingForEveryRead(t *testing.T) {
	f := publicSetup(t)
	g := collectionPeer(t, f)
	reader, credential := sameUserKey(t, f)
	current, _ := reader.GatewaySubject()
	item := collectionItem(t, "studio-image-v1")
	g.change(func(g *collectionGateway) {
		g.keys[current.KeyRef()] = true
		g.items[item.Model] = []controlclient.ListedContent{item}
	})
	headers := map[string]string{"Authorization": "Bearer " + credential}
	status, _, _ := f.call(t, "GET", "/v1/content/"+item.ID, "", headers)
	if status != 404 || g.hits("/gateway/v1/content/"+item.ID) != 0 {
		t.Fatal("unindexed ID caused remote probing")
	}
	status, raw, _ := f.call(t, "GET", "/v1/content?limit=1", "", headers)
	page := collectionPage[publicContent](t, status, raw)
	if len(page.Data) != 1 || page.Data[0].ID != item.ID || page.Data[0].JobID != item.ExternalTaskRef || bytes.Contains(raw, []byte("external_task_ref")) || bytes.Contains(raw, []byte(item.JobID)) || bytes.Contains(raw, []byte("gateway_")) {
		t.Fatal("shared list lost authorized content or exposed internal routing")
	}
	for _, method := range []string{"GET", "HEAD"} {
		downloadHeaders := map[string]string{"Authorization": "Bearer " + credential, "Range": "bytes=1-3"}
		status, raw, response := f.call(t, method, "/v1/content/"+item.ID+"/download", "", downloadHeaders)
		if status != 206 || response.Get("Content-Range") == "" || method == "GET" && !bytes.Equal(raw, fixtureImage[1:4]) || method == "HEAD" && len(raw) != 0 {
			t.Fatal("shared range read did not reauthorize current caller", method, status)
		}
	}
	status, _, _ = f.call(t, "GET", "/v1/content/"+item.ID, "", headers)
	if status != 200 {
		t.Fatal("authorized shared metadata not readable", status)
	}
	before := g.hits("/gateway/v1/content/" + item.ID)
	status, _, _ = f.call(t, "DELETE", "/v1/content/"+item.ID, "", headers)
	if status != 404 || g.hits("/gateway/v1/content/"+item.ID) != before {
		t.Fatal("discovered read route granted deletion")
	}
	status, raw, _ = f.call(t, "GET", "/v1/jobs", "", headers)
	if page := collectionPage[publicJob](t, status, raw); len(page.Data) != 0 {
		t.Fatal("shared content granted task ownership")
	}
	g.change(func(g *collectionGateway) { g.keys[current.KeyRef()] = false })
	for _, path := range []string{"/v1/content/" + item.ID, "/v1/content/" + item.ID + "/download"} {
		status, _, _ = f.call(t, "GET", path, "", headers)
		if status != 404 {
			t.Fatal("revoked shared route retained access", status)
		}
		if strings.HasSuffix(path, "/download") {
			status, _, _ = f.call(t, "HEAD", path, "", headers)
			if status != 404 {
				t.Fatal("HEAD bypassed revoked sharing", status)
			}
		}
	}
	status, raw, _ = f.call(t, "GET", "/v1/content", "", headers)
	if page := collectionPage[publicContent](t, status, raw); len(page.Data) != 0 {
		t.Fatal("revoked collection returned stale shared content")
	}
	if countRows(t, `SELECT count(*) FROM render_control_content_routes WHERE instance=$1`, f.subject.instance) != 1 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE instance=$1`, f.subject.instance) != 0 {
		t.Fatal("read index created task or ledger state")
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestContentCollectionStreamsContinueAndBindConfiguration(t *testing.T) {
	f := publicSetup(t)
	g := collectionPeer(t, f)
	for n := 1; n < 9; n++ {
		rule := f.config.Models[0]
		rule.Model = fmt.Sprintf("z-image-%d", n)
		f.config.Models = append(f.config.Models, rule)
	}
	f.start(t)
	status, raw, _ := f.call(t, "GET", "/v1/content?limit=1", "", nil)
	page := collectionPage[publicContent](t, status, raw)
	if len(page.Data) != 0 || !page.HasMore || g.hits("/gateway/v1/content") != 8 {
		t.Fatal("stream budget did not return an empty continuable page")
	}
	cursor := *page.NextCursor
	status, raw, _ = f.call(t, "GET", "/v1/content?limit=1&cursor="+url.QueryEscape(cursor), "", nil)
	if page := collectionPage[publicContent](t, status, raw); len(page.Data) != 0 || page.HasMore || g.hits("/gateway/v1/content") != 9 {
		t.Fatal("stream continuation revisited previous targets")
	}
	f.config.Models = f.config.Models[:1]
	f.start(t)
	status, _, _ = f.call(t, "GET", "/v1/content?limit=1&cursor="+url.QueryEscape(cursor), "", nil)
	if status != 400 {
		t.Fatal("configuration change accepted an old stream cursor")
	}
	first, second := collectionItem(t, "studio-image-v1"), collectionItem(t, "studio-image-v1")
	g.change(func(g *collectionGateway) { g.items[first.Model] = []controlclient.ListedContent{first, second} })
	status, raw, _ = f.call(t, "GET", "/v1/content?limit=1", "", nil)
	page = collectionPage[publicContent](t, status, raw)
	if len(page.Data) != 1 || page.Data[0].ID != first.ID || page.NextCursor == nil || strings.Contains(*page.NextCursor, "remote-page") {
		t.Fatal("remote continuation escaped opaque outer cursor")
	}
	for _, query := range []string{"limit=2", "limit=1&model=studio-image-v1"} {
		status, _, _ = f.call(t, "GET", "/v1/content?"+query+"&cursor="+url.QueryEscape(*page.NextCursor), "", nil)
		if status != 400 {
			t.Fatal("content cursor crossed its page size or filter")
		}
	}
	status, raw, _ = f.call(t, "GET", "/v1/content?limit=1&cursor="+url.QueryEscape(*page.NextCursor), "", nil)
	page = collectionPage[publicContent](t, status, raw)
	if len(page.Data) != 1 || page.Data[0].ID != second.ID || page.HasMore {
		t.Fatal("remote page cursor repeated or skipped content")
	}
}

func TestContentCollectionFailureCapacityAndCancellationRelease(t *testing.T) {
	f := publicSetup(t)
	g := collectionPeer(t, f)
	for _, failure := range []int{401, 403, 503} {
		g.change(func(g *collectionGateway) { g.failure = failure })
		status, raw, headers := f.call(t, "GET", "/v1/content", "", nil)
		if failure == 503 {
			if status != 503 || !bytes.Contains(raw, []byte(`"code":"dependency_unavailable"`)) || bytes.Contains(raw, []byte(`"object":"list"`)) || headers.Get("Retry-After") != "2" || headers.Get("Cache-Control") != "no-store" || headers.Get("X-Request-ID") == "" {
				t.Fatal("failed stream was presented as a complete page")
			}
		} else if page := collectionPage[publicContent](t, status, raw); len(page.Data) != 0 {
			t.Fatal("denied stream retained content")
		}
		if len(f.handler.listSlots) != 0 {
			t.Fatal("failed collection leaked a slot")
		}
	}
	for range cap(f.handler.listSlots) {
		f.handler.listSlots <- struct{}{}
	}
	status, _, headers := f.call(t, "GET", "/v1/content", "", nil)
	if status != 429 || headers.Get("Retry-After") != "2" {
		t.Fatal("collection capacity did not reject immediately")
	}
	for range cap(f.handler.listSlots) {
		<-f.handler.listSlots
	}
	g.change(func(g *collectionGateway) { g.failure = 0; g.block = true })
	requestContext, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, _ := http.NewRequestWithContext(requestContext, "GET", f.server.URL+"/v1/content", nil)
	request.Header.Set("Authorization", "Bearer "+f.credential)
	done := make(chan struct{})
	go func() {
		response, _ := f.server.Client().Do(request)
		if response != nil {
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
		}
		close(done)
	}()
	select {
	case <-g.entered:
	case <-time.After(3 * time.Second):
		t.Fatal("cancellation fixture did not enter collection")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("cancelled caller did not exit")
	}
	select {
	case <-g.finished:
	case <-time.After(3 * time.Second):
		t.Fatal("cancelled remote collection did not exit")
	}
	deadline := time.Now().Add(time.Second)
	for len(f.handler.listSlots) != 0 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if len(f.handler.listSlots) != 0 {
		t.Fatal("cancelled collection leaked capacity")
	}
}

func TestCollectionReadsRetainExactBillingAndAuthenticationBoundary(t *testing.T) {
	for _, condition := range []string{"balance_zero", "key_expired", "quota_exhausted", "key_disabled", "user_disabled", "ip_denied"} {
		t.Run(condition, func(t *testing.T) {
			f := publicSetup(t)
			collectionPeer(t, f)
			f.submit(t, "collection-auth")
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
			for _, path := range []string{"/v1/jobs", "/v1/content"} {
				status, _, _ := f.call(t, "GET", path, "", nil)
				if allowed && status != 200 || !allowed && status != 401 && status != 403 {
					t.Fatal("collection billing exception bypassed current authentication", path, status)
				}
			}
			status, _, _ := f.call(t, "POST", "/v1/jobs", publicImageBody, nil)
			if status == 202 {
				t.Fatal("read exception enabled new spending")
			}
		})
	}
}

func TestCollectionQueryAndHostedRouteAreStrict(t *testing.T) {
	f := publicSetup(t)
	g := collectionPeer(t, f)
	for _, path := range []string{"/v1/jobs?page=1", "/v1/content?status=queued", "/v1/content?limit=1&limit=2", "/v1/content?limit=01", "/v1/content?limit=0", "/v1/content?limit=101", "/v1/jobs?status=unknown", "/v1/jobs?model=../private", "/v1/content?cursor=", "/v1/content?limit=%FF", "/v1/content?limit=1;model=studio-image-v1"} {
		status, _, _ := f.call(t, "GET", path, "", nil)
		if status != 400 {
			t.Fatal("ignored or malformed collection query accepted", path, status)
		}
	}
	if g.hits("/gateway/v1/content") != 0 {
		t.Fatal("invalid collection query reached remote gateway")
	}
	var stockCalls atomic.Int32
	hostDiscovery(t, f, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { stockCalls.Add(1); w.WriteHeader(418) }))
	for _, path := range []string{"/v1/jobs", "/v1/content"} {
		status, _, _ := f.call(t, "GET", path, "", nil)
		if status != 200 || stockCalls.Load() != 0 {
			t.Fatal("owned collection reached stock routing", path, status)
		}
		status, _, _ = f.call(t, "GET", path, "", map[string]string{"Authorization": ""})
		if status != 401 {
			t.Fatal("anonymous collection accepted", status)
		}
	}
}

func boundCollectionIntent(t *testing.T, f *publicFixture, token string) Intent {
	t.Helper()
	i := reserve(t, f.s, f.subject, token, 1, "0.01")
	if _, err := f.s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	bound, err := f.s.BindAdmission(ctx(), i.ID, "adm_"+i.ID[3:], "job_"+i.ID[3:])
	if err != nil {
		t.Fatal(err)
	}
	return bound
}

func TestContentRouteBindingsAreImmutableAtomicAndDoNotGrantOwnership(t *testing.T) {
	f := publicSetup(t)
	i := boundCollectionIntent(t, f, "route-original")
	item := collectionItem(t, i.Model)
	item.JobID, item.ExternalTaskRef = i.RemoteJobID, i.ID
	reader, _ := sameUserKey(t, f)
	for range 2 {
		if err := f.s.bindDiscoveredContent(ctx(), reader, i.TargetGatewayID, []controlclient.ListedContent{item}); err != nil {
			t.Fatal("authorized route replay failed", err)
		}
	}
	if _, err := f.s.loadContent(ctx(), reader, item.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("read route granted original ownership")
	}
	if err := f.s.bindContent(ctx(), f.subject, i.ID, []controlclient.Artifact{{ID: item.ID}}); err != nil {
		t.Fatal("matching original binding rejected", err)
	}
	extra := collectionItem(t, i.Model)
	changed := item
	changed.ExternalTaskRef = extra.ExternalTaskRef
	if err := f.s.bindDiscoveredContent(ctx(), reader, i.TargetGatewayID, []controlclient.ListedContent{extra, changed}); !errors.Is(err, ErrConflict) {
		t.Fatal("conflicting batch route was accepted", err)
	}
	if countRows(t, `SELECT count(*) FROM render_control_content_routes WHERE instance=$1`, f.subject.instance) != 1 {
		t.Fatal("conflicting batch left partial routes")
	}
	if _, err := testDB.Exec(`UPDATE render_control_content_routes SET public_model=public_model WHERE instance=$1`, f.subject.instance); err == nil {
		t.Fatal("database permitted content route update")
	}
	other := boundCollectionIntent(t, f, "route-other")
	unknown := collectionItem(t, other.Model)
	unknown.JobID, unknown.ExternalTaskRef = other.RemoteJobID, other.ID
	if err := f.s.bindDiscoveredContent(ctx(), reader, other.TargetGatewayID, []controlclient.ListedContent{unknown}); err != nil {
		t.Fatal(err)
	}
	if _, err := testDB.Exec(`INSERT INTO render_control_content_bindings(instance,content_id,intent_id) VALUES($1,$2,$3)`, f.subject.instance, unknown.ID, i.ID); err == nil {
		t.Fatal("database allowed original binding to contradict discovered route")
	}
	owned := collectionItem(t, i.Model)
	if err := f.s.bindContent(ctx(), f.subject, i.ID, []controlclient.Artifact{{ID: owned.ID}}); err != nil {
		t.Fatal(err)
	}
	if _, err := testDB.Exec(`INSERT INTO render_control_content_routes(instance,content_id,target_gateway_id,remote_job_id,external_task_ref,public_model,task_type) VALUES($1,$2,$3,$4,$5,$6,$7)`, f.subject.instance, owned.ID, other.TargetGatewayID, other.RemoteJobID, other.ID, other.Model, other.TaskType); err == nil {
		t.Fatal("database allowed discovered route to contradict original binding")
	}
	foreign := reader
	foreign.instance = "other-instance"
	if _, err := f.s.loadContentRoute(ctx(), foreign, item.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("route lookup crossed Provider instance")
	}
}

func TestContentRouteConcurrentDiscoveryAndOriginalBindingCannotDisagree(t *testing.T) {
	f := publicSetup(t)
	first := boundCollectionIntent(t, f, "route-race-first")
	second := boundCollectionIntent(t, f, "route-race-second")
	for range 4 {
		item := collectionItem(t, second.Model)
		item.JobID, item.ExternalTaskRef = second.RemoteJobID, second.ID
		start := make(chan struct{})
		results := make(chan error, 2)
		go func() {
			<-start
			results <- f.s.bindDiscoveredContent(ctx(), f.subject, second.TargetGatewayID, []controlclient.ListedContent{item})
		}()
		go func() {
			<-start
			results <- f.s.bindContent(ctx(), f.subject, first.ID, []controlclient.Artifact{{ID: item.ID}})
		}()
		close(start)
		one, two := <-results, <-results
		if (one == nil) == (two == nil) || one != nil && !errors.Is(one, ErrConflict) || two != nil && !errors.Is(two, ErrConflict) {
			t.Fatal("concurrent content indexes disagreed or deadlocked", one, two)
		}
		if countRows(t, `SELECT count(*) FROM render_control_content_routes r JOIN render_control_content_bindings b USING(instance,content_id) JOIN render_control_intents i ON i.id=b.intent_id WHERE r.instance=$1 AND (r.remote_job_id,r.external_task_ref) IS DISTINCT FROM (i.remote_job_id,i.id)`, f.subject.instance) != 0 {
			t.Fatal("concurrent route conflict persisted")
		}
	}
}
