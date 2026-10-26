package rendercontrol

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

//go:embed testdata/dependency-error-policy.json
var dependencyErrorPolicy []byte

func TestDependencyErrorProjectionPreservesStatusAndUncertainWrites(t *testing.T) {
	var policy struct {
		Provider struct {
			Status  int    `json:"status"`
			Code    string `json:"code"`
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"provider"`
		Headers map[string]string `json:"headers"`
	}
	if json.Unmarshal(dependencyErrorPolicy, &policy) != nil || policy.Provider.Status != 503 {
		t.Fatal("dependency error policy fixture invalid")
	}
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		for _, status := range []int{503, 500, 504, 403, 404, 409, 429} {
			t.Run(fmt.Sprintf("%s/%d", method, status), func(t *testing.T) {
				call := &controlclient.CallError{Code: "http_error", HTTPStatus: status, RemoteCode: "PRIVATE-REMOTE", Uncertain: method == http.MethodPost && status >= 500}
				before := *call
				writer := httptest.NewRecorder()
				c, _ := gin.CreateTestContext(writer)
				c.Request = httptest.NewRequest(method, "/v1/jobs", nil)
				publicError(c, fmt.Errorf("PRIVATE-WRAPPED: %w", call))
				if *call != before || bytes.Contains(writer.Body.Bytes(), []byte("PRIVATE")) {
					t.Fatal("error projection changed uncertainty or leaked remote details")
				}
				var body struct {
					Error struct {
						Code    string `json:"code"`
						Type    string `json:"type"`
						Message string `json:"message"`
					} `json:"error"`
				}
				if json.Unmarshal(writer.Body.Bytes(), &body) != nil {
					t.Fatal("public error envelope invalid")
				}
				if status == 503 {
					if writer.Code != policy.Provider.Status || body.Error.Code != policy.Provider.Code || body.Error.Type != policy.Provider.Type || body.Error.Message != policy.Provider.Message || closeAfterAdmissionError(call) {
						t.Fatal("dependency outage was hidden or interpreted as closure proof")
					}
					for name, value := range policy.Headers {
						if writer.Header().Get(name) != value {
							t.Fatal("dependency outage header differs from frozen policy")
						}
					}
				} else {
					expected := status
					if status >= 500 {
						expected = 502
					}
					if writer.Code != expected || writer.Header().Get("Retry-After") != "" || body.Error.Code == policy.Provider.Code {
						t.Fatal("unrelated error was reclassified as a dependency outage")
					}
				}
			})
		}
	}
}

func TestDependency503AfterAcceptedAdmissionKeepsOriginalIntentAndHold(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "dependency-accepted-admission", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) { g.admission503AfterAccept = true })
	c := testCoordinator(t, s, subject, g, "before-dependency-restart")
	dueRecovery(t, i.ID)
	claimed, err := c.Step(ctx())
	var call *controlclient.CallError
	if !claimed || !errors.As(err, &call) || call.HTTPStatus != 503 || !call.Uncertain || closeAfterAdmissionError(err) {
		t.Fatal("HTTP 503 lost unknown-write semantics")
	}
	loaded, loadErr := s.Load(ctx(), subject, i.ID)
	if loadErr != nil || loaded.State != "dispatching" || loaded.DispatchCount != 1 || loaded.AdmissionID != "" || loaded.ProviderRequestID != i.ProviderRequestID || loaded.RequestHash != i.RequestHash {
		t.Fatal("unknown 503 changed original persisted intent")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	c = testCoordinator(t, store(t), subject, g, "after-dependency-restart")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, false)
	if g.count(admissionPath) != 1 || g.count(lookupPath) < 1 || g.count(closePath) != 0 || g.count(admissionPath+"/adm_"+i.ID[3:]+"/confirm") != 1 {
		t.Fatal("503 recovery resent or closed accepted admission")
	}
	g.configure(func(g *recoveryGateway) { g.outcome = "succeeded" })
	batch, err := c.ReconcileSettlements(ctx())
	if err != nil || batch.Resolved != 1 || batch.Acknowledged != 1 {
		t.Fatal("recovered 503 intent could not settle exactly once")
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
	batch, err = c.ReconcileSettlements(ctx())
	if err != nil || batch.Resolved != 0 || batch.Acknowledged != 0 {
		t.Fatal("replayed 503 settlement was consumed twice")
	}
}
