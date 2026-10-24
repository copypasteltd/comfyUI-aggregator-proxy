// Package controlclient implements the Render Control metadata protocol.
// Callers must persist intent and billing before admission; this is not a Provider.
package controlclient

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var credentialPattern = regexp.MustCompile(`^gateway_[a-f0-9]{32}\.[A-Za-z0-9_-]{43,128}$`)
var instancePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{1,63}$`)
var errorPattern = regexp.MustCompile(`^[a-z0-9_]{1,64}$`)

type Credential struct{ value string }

func ParseCredential(value string) (Credential, error) {
	if !credentialPattern.MatchString(value) {
		return Credential{}, errors.New("invalid gateway credential")
	}
	return Credential{value: value}, nil
}

func (Credential) String() string   { return "[redacted]" }
func (Credential) GoString() string { return "[redacted]" }

// Subject is constructed from authenticated Sub2API records, never public headers.
type Subject struct{ tenant, key, ip string }

func NewSubject(instance string, userID, keyID int64, clientIP netip.Addr) (Subject, error) {
	if !instancePattern.MatchString(instance) || userID < 1 || keyID < 1 || !clientIP.IsValid() || clientIP.Zone() != "" {
		return Subject{}, errors.New("invalid authenticated subject")
	}
	return Subject{tenant: fmt.Sprintf("%s:user:%d", instance, userID), key: fmt.Sprintf("%s:key:%d", instance, keyID), ip: clientIP.Unmap().String()}, nil
}

func (s Subject) TenantRef() string { return s.tenant }
func (s Subject) KeyRef() string    { return s.key }

type Config struct {
	GatewayID         string
	Origin            string
	Credential        Credential
	RootCAs           *x509.CertPool
	AllowLoopbackHTTP bool
	Timeout           time.Duration
	MaxResponseBytes  int64
}

func (Config) String() string   { return "controlclient.Config{redacted}" }
func (Config) GoString() string { return "controlclient.Config{redacted}" }

type Client struct {
	gatewayID string
	origin    string
	secure    bool
	secret    Credential
	http      *http.Client
	limit     int64
}

func (*Client) String() string   { return "controlclient.Client{redacted}" }
func (*Client) GoString() string { return "controlclient.Client{redacted}" }

// CallError contains only safe protocol codes. Uncertain requires reconciliation
// under the original persisted intent, rather than a newly generated request ID.
type CallError struct {
	Code       string
	HTTPStatus int
	RemoteCode string
	Uncertain  bool
	cause      error
}

func (e *CallError) Error() string {
	return fmt.Sprintf("render control: %s (HTTP %d, code %s)", e.Code, e.HTTPStatus, e.RemoteCode)
}
func (e *CallError) Unwrap() error    { return e.cause }
func (e *CallError) GoString() string { return e.Error() }

func New(config Config) (*Client, error) {
	if !identifier("gateway", config.GatewayID) {
		return nil, errors.New("stable gateway service ID required")
	}
	origin, err := url.Parse(config.Origin)
	if err != nil || origin.Hostname() == "" || origin.User != nil || origin.RawQuery != "" || origin.ForceQuery || origin.Fragment != "" || origin.RawFragment != "" || (origin.Path != "" && origin.Path != "/") || origin.Opaque != "" {
		return nil, errors.New("gateway origin must be a plain HTTP(S) origin")
	}
	loopback, loopErr := netip.ParseAddr(origin.Hostname())
	if origin.Scheme != "https" && !(origin.Scheme == "http" && config.AllowLoopbackHTTP && loopErr == nil && loopback.IsLoopback()) {
		return nil, errors.New("gateway requires HTTPS; HTTP is restricted to explicit loopback tests")
	}
	if !credentialPattern.MatchString(config.Credential.value) {
		return nil, errors.New("invalid gateway credential")
	}
	if config.Timeout == 0 {
		config.Timeout = 30 * time.Second
	}
	if config.MaxResponseBytes == 0 {
		config.MaxResponseBytes = 2 << 20
	}
	if config.Timeout < time.Millisecond || config.Timeout > time.Minute || config.MaxResponseBytes < 1024 || config.MaxResponseBytes > 8<<20 {
		return nil, errors.New("invalid metadata request limits")
	}
	var rootCAs *x509.CertPool
	if config.RootCAs != nil {
		rootCAs = config.RootCAs.Clone()
	}
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext, MaxConnsPerHost: 8, MaxIdleConns: 8, MaxIdleConnsPerHost: 4, IdleConnTimeout: 30 * time.Second, TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 10 * time.Second, MaxResponseHeaderBytes: 64 << 10, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: rootCAs}}
	return &Client{gatewayID: config.GatewayID, origin: strings.TrimSuffix(config.Origin, "/"), secure: origin.Scheme == "https", secret: config.Credential, limit: config.MaxResponseBytes, http: &http.Client{Transport: transport, Timeout: config.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

func (c *Client) Close() { c.http.CloseIdleConnections() }

// GatewayID is the explicitly configured stable service ID. Credential IDs are
// independent and change on rotation; they must never identify a ledger target.
func (c *Client) GatewayID() string { return c.gatewayID }
func (c *Client) HTTPS() bool       { return c.secure }

func ascii(value string, minimum, maximum int) bool {
	if len(value) < minimum || len(value) > maximum {
		return false
	}
	for _, character := range value {
		if character < 33 || character > 126 {
			return false
		}
	}
	return true
}

func identifier(prefix, value string) bool {
	if !strings.HasPrefix(value, prefix+"_") || len(value) != len(prefix)+33 {
		return false
	}
	for _, character := range value[len(prefix)+1:] {
		if !(character >= '0' && character <= '9' || character >= 'a' && character <= 'f') {
			return false
		}
	}
	return true
}

func (c *Client) call(ctx context.Context, method, path string, subject *Subject, body any, result any) error {
	var payload []byte
	var err error
	if body != nil {
		payload, err = json.Marshal(body)
		if err != nil || len(payload) > 2<<20 {
			return &CallError{Code: "invalid_request"}
		}
	}
	request, err := http.NewRequestWithContext(ctx, method, c.origin+path, bytes.NewReader(payload))
	if err != nil {
		return &CallError{Code: "invalid_request"}
	}
	request.Header.Set("Authorization", "Bearer "+c.secret.value)
	request.Header.Set("Accept", "application/json")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if subject != nil {
		if subject.tenant == "" || subject.key == "" || subject.ip == "" {
			return &CallError{Code: "invalid_subject"}
		}
		request.Header.Set("X-External-Tenant-Ref", subject.tenant)
		request.Header.Set("X-External-Key-Ref", subject.key)
		request.Header.Set("X-Client-IP", subject.ip)
	}
	uncertain := method != http.MethodGet && method != http.MethodHead
	response, err := c.http.Do(request)
	if err != nil {
		return &CallError{Code: "transport_error", Uncertain: uncertain, cause: err}
	}
	defer response.Body.Close()
	if response.ContentLength > c.limit {
		return &CallError{Code: "response_too_large", HTTPStatus: response.StatusCode, Uncertain: uncertain}
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, c.limit+1))
	if int64(len(data)) > c.limit {
		return &CallError{Code: "response_too_large", HTTPStatus: response.StatusCode, Uncertain: uncertain}
	}
	if err != nil {
		return &CallError{Code: "invalid_response", HTTPStatus: response.StatusCode, Uncertain: uncertain}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var remote struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		_ = json.Unmarshal(data, &remote)
		code := remote.Error.Code
		if !errorPattern.MatchString(code) {
			code = "http_error"
		}
		return &CallError{Code: "http_error", HTTPStatus: response.StatusCode, RemoteCode: code, Uncertain: uncertain && (response.StatusCode >= 500 || response.StatusCode == 408 || response.StatusCode >= 300 && response.StatusCode < 400)}
	}
	if err := json.Unmarshal(data, result); err != nil {
		return &CallError{Code: "invalid_response", HTTPStatus: response.StatusCode, Uncertain: uncertain}
	}
	return nil
}

type JobRequest struct {
	Model           string         `json:"model"`
	TaskType        string         `json:"task_type"`
	Input           map[string]any `json:"input"`
	Parameters      map[string]any `json:"parameters,omitempty"`
	ClientRequestID string         `json:"client_request_id,omitempty"`
}

type AdmissionRequest struct {
	ProviderRequestID string        `json:"provider_request_id"`
	ExternalTaskRef   string        `json:"external_task_ref"`
	PriceVersion      string        `json:"price_version"`
	AuthorizationRef  string        `json:"authorization_ref"`
	Request           JobRequest    `json:"request"`
	Catalog           *CatalogStamp `json:"catalog,omitempty"`
}

type Admission struct {
	ID                string  `json:"id"`
	JobID             string  `json:"job_id"`
	ProviderRequestID string  `json:"provider_request_id"`
	ExternalTaskRef   string  `json:"external_task_ref"`
	Status            string  `json:"status"`
	JobStatus         string  `json:"job_status"`
	ExpiresAt         int64   `json:"expires_at"`
	ErrorCode         *string `json:"error_code"`
}

func jobStatus(value string) bool {
	switch value {
	case "admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired":
		return true
	}
	return false
}

func validAdmission(value Admission) bool {
	if !identifier("adm", value.ID) || !identifier("job", value.JobID) || !ascii(value.ProviderRequestID, 16, 128) || !ascii(value.ExternalTaskRef, 1, 128) || value.ExpiresAt < 1 || !jobStatus(value.JobStatus) || value.ErrorCode != nil && !errorPattern.MatchString(*value.ErrorCode) {
		return false
	}
	switch value.Status {
	case "pending":
		return value.JobStatus == "admission_pending"
	case "confirmed":
		return value.JobStatus != "admission_pending"
	case "aborted":
		return value.JobStatus == "cancelled"
	case "expired":
		return value.JobStatus == "expired"
	case "rejected":
		return value.JobStatus == "failed"
	}
	return false
}

func (c *Client) Admit(ctx context.Context, subject Subject, data AdmissionRequest) (Admission, error) {
	if !ascii(data.ProviderRequestID, 16, 128) || !ascii(data.ExternalTaskRef, 1, 128) || !ascii(data.PriceVersion, 1, 64) || !ascii(data.AuthorizationRef, 1, 128) || data.Catalog != nil && !data.Catalog.Valid() {
		return Admission{}, &CallError{Code: "invalid_request"}
	}
	var result Admission
	err := c.call(ctx, http.MethodPost, "/gateway/v1/admissions", &subject, data, &result)
	if err == nil && (!validAdmission(result) || result.ProviderRequestID != data.ProviderRequestID || result.ExternalTaskRef != data.ExternalTaskRef) {
		err = &CallError{Code: "invalid_response", Uncertain: true}
	}
	return result, err
}

func (c *Client) admissionAction(ctx context.Context, subject Subject, id, action string) (Admission, error) {
	if !identifier("adm", id) {
		return Admission{}, &CallError{Code: "invalid_identifier"}
	}
	var result Admission
	err := c.call(ctx, http.MethodPost, "/gateway/v1/admissions/"+id+"/"+action, &subject, nil, &result)
	if err == nil && (result.ID != id || !validAdmission(result) || action == "confirm" && result.Status != "confirmed" || action == "abort" && result.Status == "confirmed") {
		err = &CallError{Code: "invalid_response", Uncertain: true}
	}
	return result, err
}

func (c *Client) Confirm(ctx context.Context, subject Subject, id string) (Admission, error) {
	return c.admissionAction(ctx, subject, id, "confirm")
}
func (c *Client) Abort(ctx context.Context, subject Subject, id string) (Admission, error) {
	return c.admissionAction(ctx, subject, id, "abort")
}

// RequestIdentity contains only persisted admission references and authenticated
// subject references. It grants no current customer or content authority.
type RequestIdentity struct {
	ProviderRequestID string `json:"provider_request_id"`
	ExternalTaskRef   string `json:"external_task_ref"`
	PriceVersion      string `json:"price_version"`
	AuthorizationRef  string `json:"authorization_ref"`
	ExternalTenantRef string `json:"external_tenant_ref"`
	ExternalKeyRef    string `json:"external_key_ref"`
	gatewayID         string
}

func NewRequestIdentity(expectedGatewayID string, subject Subject, request AdmissionRequest) (RequestIdentity, error) {
	result := RequestIdentity{request.ProviderRequestID, request.ExternalTaskRef, request.PriceVersion, request.AuthorizationRef, subject.tenant, subject.key, expectedGatewayID}
	if !validRequestIdentity(result) {
		return RequestIdentity{}, &CallError{Code: "invalid_request"}
	}
	return result, nil
}

func validRequestIdentity(value RequestIdentity) bool {
	return identifier("gateway", value.gatewayID) && ascii(value.ProviderRequestID, 16, 128) && ascii(value.ExternalTaskRef, 1, 128) && ascii(value.PriceVersion, 1, 64) && ascii(value.AuthorizationRef, 1, 128) && ascii(value.ExternalTenantRef, 1, 128) && ascii(value.ExternalKeyRef, 1, 128)
}

type RequestResolution struct {
	RequestIdentity
	GatewayID string     `json:"gateway_id"`
	Status    string     `json:"status"`
	ClosureID *string    `json:"closure_id"`
	Admission *Admission `json:"admission"`
	proof     ClosureProof
}

// ClosureProof is issued only after an authenticated HTTPS response passes all
// identity and state checks. JSON cannot recreate this in-process capability.
// Persist its metadata together with the ledger transition, never the token.
type ClosureProof struct {
	identity RequestIdentity
	id       string
}

func (ClosureProof) String() string   { return "controlclient.ClosureProof{redacted}" }
func (ClosureProof) GoString() string { return "controlclient.ClosureProof{redacted}" }

func (p ClosureProof) Matches(identity RequestIdentity) bool {
	return validRequestIdentity(identity) && identifier("gcl", p.id) && p.identity == identity
}

func (p ClosureProof) ID() string { return p.id }

func (r RequestResolution) ClosureProof() (ClosureProof, bool) {
	return r.proof, r.proof.Matches(r.proof.identity)
}

func validRequestResolution(value RequestResolution, request RequestIdentity, closing bool) bool {
	// The expected target belongs to the persisted intent, not a newly rotated
	// credential or mutable current routing configuration. It is not sent as
	// customer authority and cannot be supplied by the server's echo.
	expectedGateway := request.gatewayID
	request.gatewayID = ""
	if value.RequestIdentity != request || value.GatewayID != expectedGateway {
		return false
	}
	switch value.Status {
	case "unknown":
		// An absence observation is never a closure or a refund authorization.
		return !closing && value.ClosureID == nil && value.Admission == nil
	case "closed":
		return value.ClosureID != nil && identifier("gcl", *value.ClosureID) && value.Admission == nil
	case "admitted":
		return value.ClosureID == nil && value.Admission != nil && validAdmission(*value.Admission) && value.Admission.ProviderRequestID == request.ProviderRequestID && value.Admission.ExternalTaskRef == request.ExternalTaskRef && (!closing || value.Admission.Status != "pending")
	}
	return false
}

// LookupRequest performs no automatic retry and carries no customer authority
// headers. Even a successful "unknown" response cannot release a local hold.
func (c *Client) LookupRequest(ctx context.Context, request RequestIdentity) (RequestResolution, error) {
	return c.resolveRequest(ctx, request, false)
}

// CloseRequest durably excludes delayed creation or returns the original
// admission. A confirmed task is not cancelled by this reconciliation API.
func (c *Client) CloseRequest(ctx context.Context, request RequestIdentity) (RequestResolution, error) {
	return c.resolveRequest(ctx, request, true)
}

func (c *Client) resolveRequest(ctx context.Context, request RequestIdentity, closing bool) (RequestResolution, error) {
	if !validRequestIdentity(request) {
		return RequestResolution{}, &CallError{Code: "invalid_request"}
	}
	action := "lookup"
	if closing {
		action = "close"
	}
	var result RequestResolution
	err := c.call(ctx, http.MethodPost, "/gateway/v1/admission-requests/"+action, nil, request, &result)
	if err != nil {
		// Both operations use POST to keep authorization references out of URLs.
		// Only close can mutate a fence or admission when a response is lost.
		var failure *CallError
		if !closing && errors.As(err, &failure) {
			failure.Uncertain = false
		}
		return RequestResolution{}, err
	}
	if !validRequestResolution(result, request, closing) {
		return RequestResolution{}, &CallError{Code: "invalid_response", Uncertain: closing}
	}
	if result.Status == "closed" && c.secure {
		result.proof = ClosureProof{identity: request, id: *result.ClosureID}
	}
	return result, nil
}

type Artifact struct {
	ID string `json:"id"`
}
type Job struct {
	ID              string     `json:"id"`
	Status          string     `json:"status"`
	Model           string     `json:"model"`
	TaskType        string     `json:"task_type"`
	UpdatedRevision int64      `json:"updated_revision"`
	Artifacts       []Artifact `json:"artifacts"`
}

func (c *Client) Job(ctx context.Context, subject Subject, id string) (Job, error) {
	return c.jobAction(ctx, subject, id, false)
}
func (c *Client) Cancel(ctx context.Context, subject Subject, id string) (Job, error) {
	return c.jobAction(ctx, subject, id, true)
}
func (c *Client) jobAction(ctx context.Context, subject Subject, id string, cancel bool) (Job, error) {
	if !identifier("job", id) {
		return Job{}, &CallError{Code: "invalid_identifier"}
	}
	method, path := http.MethodGet, "/gateway/v1/jobs/"+id
	if cancel {
		method, path = http.MethodPost, path+"/cancel"
	}
	var result Job
	err := c.call(ctx, method, path, &subject, nil, &result)
	if err == nil && (result.ID != id || !jobStatus(result.Status) || result.UpdatedRevision < 1 || len(result.Artifacts) > 100 || len(result.Artifacts) > 0 && result.Status != "succeeded") {
		err = &CallError{Code: "invalid_response", Uncertain: cancel}
	}
	if err == nil {
		seen := map[string]bool{}
		for _, artifact := range result.Artifacts {
			if !identifier("content", artifact.ID) || seen[artifact.ID] {
				err = &CallError{Code: "invalid_response", Uncertain: cancel}
				break
			}
			seen[artifact.ID] = true
		}
	}
	return result, err
}

type Settlement struct {
	ID                string `json:"id"`
	JobID             string `json:"job_id"`
	ExternalTaskRef   string `json:"external_task_ref"`
	AuthorizationRef  string `json:"authorization_ref"`
	PriceVersion      string `json:"price_version"`
	Outcome           string `json:"outcome"`
	ImageCount        int64  `json:"image_count"`
	VideoMilliseconds int64  `json:"video_milliseconds"`
	AudioMilliseconds int64  `json:"audio_milliseconds,omitempty"`
}

func (c *Client) Settlements(ctx context.Context) ([]Settlement, error) {
	var result struct {
		Data           []Settlement `json:"data"`
		MonetaryLedger string       `json:"monetary_ledger"`
	}
	if err := c.call(ctx, http.MethodGet, "/gateway/v1/settlements", nil, nil, &result); err != nil {
		return nil, err
	}
	if result.MonetaryLedger != "sub2api" || len(result.Data) > 100 {
		return nil, &CallError{Code: "invalid_response"}
	}
	seen := make(map[string]bool, len(result.Data))
	for _, event := range result.Data {
		if !identifier("evt", event.ID) || !identifier("job", event.JobID) || !ascii(event.ExternalTaskRef, 1, 128) || !ascii(event.AuthorizationRef, 1, 128) || !ascii(event.PriceVersion, 1, 64) || event.ImageCount < 0 || event.VideoMilliseconds < 0 || event.AudioMilliseconds < 0 || event.AudioMilliseconds > 600000 || (event.Outcome != "succeeded" && event.Outcome != "failed" && event.Outcome != "cancelled" && event.Outcome != "expired") {
			return nil, &CallError{Code: "invalid_response"}
		}
		if seen[event.ID] {
			return nil, &CallError{Code: "invalid_response"}
		}
		seen[event.ID] = true
	}
	return result.Data, nil
}

func (c *Client) Acknowledge(ctx context.Context, id, ledgerRef string) error {
	if !identifier("evt", id) || !ascii(ledgerRef, 1, 128) {
		return &CallError{Code: "invalid_request"}
	}
	var result struct {
		Accepted bool   `json:"accepted"`
		ID       string `json:"id"`
	}
	if err := c.call(ctx, http.MethodPost, "/gateway/v1/settlements/"+id+"/ack", nil, map[string]string{"ledger_ref": ledgerRef}, &result); err != nil {
		return err
	}
	if !result.Accepted || result.ID != id {
		return &CallError{Code: "invalid_response", Uncertain: true}
	}
	return nil
}
