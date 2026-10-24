package controlclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"
)

const MaxGatewayCatalogBytes = 512 << 10

type CatalogStamp struct {
	InstanceID string `json:"instance_id"`
	Revision   int64  `json:"revision"`
	Digest     string `json:"digest"`
}

func (s CatalogStamp) Valid() bool {
	return instancePattern.MatchString(s.InstanceID) && s.Revision >= 1 && s.Revision <= 2147483647 && sha256Pattern.MatchString(s.Digest)
}

type GatewayCatalogBinding struct {
	ModelID       string         `json:"model_id"`
	Revision      int64          `json:"revision"`
	TenantID      string         `json:"tenant_id"`
	ProjectID     string         `json:"project_id"`
	Alias         string         `json:"alias"`
	TaskType      string         `json:"task_type"`
	ReleaseID     string         `json:"release_id"`
	ReleaseDigest string         `json:"release_digest"`
	ChannelIDs    []string       `json:"channel_ids"`
	Defaults      map[string]any `json:"defaults"`
}

type GatewayCatalogRule struct {
	Model          string                  `json:"model"`
	TaskType       string                  `json:"task_type"`
	UsageAccountID string                  `json:"usage_account_id"`
	GroupIDs       []string                `json:"group_ids"`
	PriceVersion   string                  `json:"price_version"`
	UnitPrice      string                  `json:"unit_price"`
	Defaults       map[string]any          `json:"defaults"`
	ModelBindings  []GatewayCatalogBinding `json:"model_bindings"`
}

type GatewayCatalogConfiguration struct {
	FormatVersion string               `json:"format_version"`
	GatewayID     string               `json:"gateway_id"`
	InstanceID    string               `json:"instance_id"`
	Revision      int64                `json:"revision"`
	Models        []GatewayCatalogRule `json:"models"`
}

type GatewayCatalogRelease struct {
	GatewayID         string `json:"gateway_id"`
	Revision          int64  `json:"revision"`
	InstanceID        string `json:"instance_id"`
	Digest            string `json:"digest"`
	Action            string `json:"action"`
	SourceRevision    *int64 `json:"source_revision"`
	CreatedAt         int64  `json:"created_at"`
	ConfigurationJSON string `json:"configuration_json"`
}

func (r GatewayCatalogRelease) Stamp() CatalogStamp {
	return CatalogStamp{r.InstanceID, r.Revision, r.Digest}
}

type GatewayCatalogReceipt struct {
	InstanceID string  `json:"instance_id"`
	Revision   int64   `json:"revision"`
	Digest     string  `json:"digest"`
	Status     string  `json:"status"`
	ErrorCode  *string `json:"error_code"`
}

type GatewayCatalogReceiptView struct {
	InstanceID string  `json:"instance_id"`
	Revision   int64   `json:"revision"`
	Digest     string  `json:"digest"`
	Status     string  `json:"status"`
	ErrorCode  *string `json:"error_code"`
	ObservedAt int64   `json:"observed_at"`
}

type GatewayCatalogState struct {
	GatewayID      string                     `json:"gateway_id"`
	Revision       int64                      `json:"revision"`
	InstanceID     *string                    `json:"instance_id"`
	Managed        bool                       `json:"managed"`
	Status         string                     `json:"status"`
	DesiredStale   bool                       `json:"desired_stale"`
	ConfirmedStale bool                       `json:"confirmed_stale"`
	Desired        *GatewayCatalogRelease     `json:"desired"`
	Confirmed      *GatewayCatalogRelease     `json:"confirmed"`
	LastReceipt    *GatewayCatalogReceiptView `json:"last_receipt"`
}

type GatewayCatalogReceiptResult struct {
	Receipt   GatewayCatalogReceiptView `json:"receipt"`
	Activated bool                      `json:"activated"`
	State     GatewayCatalogState       `json:"state"`
}

// Check exact keys, required presence and nullability before encoding/json's
// case-insensitive struct projection. Recursive maps remain intentionally open.
func catalogShape(raw json.RawMessage, typ reflect.Type) bool {
	if typ.Kind() == reflect.Pointer {
		return bytes.Equal(raw, []byte("null")) || catalogShape(raw, typ.Elem())
	}
	if bytes.Equal(raw, []byte("null")) {
		return false
	}
	switch typ.Kind() {
	case reflect.Struct:
		var object map[string]json.RawMessage
		if json.Unmarshal(raw, &object) != nil || object == nil {
			return false
		}
		if len(object) != typ.NumField() {
			return false
		}
		for i := 0; i < typ.NumField(); i++ {
			field := typ.Field(i)
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			value, ok := object[name]
			if !ok || !catalogShape(value, field.Type) {
				return false
			}
		}
	case reflect.Slice:
		var items []json.RawMessage
		if json.Unmarshal(raw, &items) != nil || items == nil {
			return false
		}
		for _, item := range items {
			if !catalogShape(item, typ.Elem()) {
				return false
			}
		}
	case reflect.Map:
		var object map[string]json.RawMessage
		if json.Unmarshal(raw, &object) != nil || object == nil {
			return false
		}
	}
	return true
}

func decodeGatewayCatalog(raw []byte, result any) error {
	if catalogJSON(raw) != nil || !catalogShape(raw, reflect.TypeOf(result).Elem()) {
		return errors.New("invalid catalog shape")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	return decoder.Decode(result)
}

var gatewayAliasPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$`)
var gatewayPriceVersionPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$`)
var gatewayPricePattern = regexp.MustCompile(`^(?:0\.[0-9]{1,8}|[1-9][0-9]{0,5}(?:\.[0-9]{1,8})?|1000000(?:\.0{1,8})?)$`)

func PositiveCatalogID(value string) (int64, bool) {
	id, err := strconv.ParseInt(value, 10, 64)
	return id, err == nil && id > 0 && strconv.FormatInt(id, 10) == value
}

func catalogTask(task string) bool {
	switch task {
	case "text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio":
		return true
	}
	return false
}

func sameCatalogDefaults(a, b map[string]any) bool {
	x, e1 := json.Marshal(a)
	y, e2 := json.Marshal(b)
	return e1 == nil && e2 == nil && bytes.Equal(x, y)
}

// ParseGatewayCatalog checks the original UTF-8 bytes; it never re-serializes
// configuration_json to derive its digest or uses a floating-point price.
func ParseGatewayCatalog(release GatewayCatalogRelease, gateway, instance string) (GatewayCatalogConfiguration, error) {
	var config GatewayCatalogConfiguration
	fail := func() (GatewayCatalogConfiguration, error) {
		return GatewayCatalogConfiguration{}, &CallError{Code: "invalid_catalog"}
	}
	if release.GatewayID != gateway || release.InstanceID != instance || !identifier("gateway", gateway) || !release.Stamp().Valid() || release.CreatedAt < 1 || release.CreatedAt > 253402300799 || len(release.ConfigurationJSON) > MaxGatewayCatalogBytes {
		return fail()
	}
	if release.Action == "publish" {
		if release.SourceRevision != nil {
			return fail()
		}
	} else if release.Action != "retry" && release.Action != "rollback" || release.SourceRevision == nil || *release.SourceRevision < 1 || *release.SourceRevision >= release.Revision {
		return fail()
	}
	hash := sha256.Sum256([]byte(release.ConfigurationJSON))
	if hex.EncodeToString(hash[:]) != release.Digest || decodeGatewayCatalog([]byte(release.ConfigurationJSON), &config) != nil || config.FormatVersion != "gateway-catalog-v1" || config.GatewayID != gateway || config.InstanceID != instance || config.Revision != release.Revision || len(config.Models) > 256 {
		return fail()
	}
	aliases, bindings := map[string]bool{}, map[string]bool{}
	groupsTotal, bindingsTotal := 0, 0
	for _, rule := range config.Models {
		_, accountOK := PositiveCatalogID(rule.UsageAccountID)
		priceNonzero := strings.Trim(rule.UnitPrice, "0.") != ""
		if !gatewayAliasPattern.MatchString(rule.Model) || aliases[rule.Model] || !catalogTask(rule.TaskType) || !accountOK || !gatewayPriceVersionPattern.MatchString(rule.PriceVersion) || !gatewayPricePattern.MatchString(rule.UnitPrice) || !priceNonzero || len(rule.GroupIDs) < 1 || len(rule.GroupIDs) > 1024 || len(rule.ModelBindings) < 1 || len(rule.ModelBindings) > 64 || len(rule.Defaults) > 32 {
			return fail()
		}
		aliases[rule.Model] = true
		groups := map[string]bool{}
		for _, value := range rule.GroupIDs {
			if _, ok := PositiveCatalogID(value); !ok || groups[value] {
				return fail()
			}
			groups[value] = true
		}
		groupsTotal += len(groups)
		bindingsTotal += len(rule.ModelBindings)
		if groupsTotal > 4096 || bindingsTotal > 512 {
			return fail()
		}
		for _, binding := range rule.ModelBindings {
			if !identifier("mdl", binding.ModelID) || bindings[binding.ModelID] || binding.Revision < 1 || binding.Revision > 2147483647 || !identifier("ten", binding.TenantID) || !identifier("prj", binding.ProjectID) || binding.Alias != rule.Model || binding.TaskType != rule.TaskType || !identifier("wfr", binding.ReleaseID) || !sha256Pattern.MatchString(binding.ReleaseDigest) || len(binding.Defaults) > 32 || !sameCatalogDefaults(binding.Defaults, rule.Defaults) || len(binding.ChannelIDs) < 1 || len(binding.ChannelIDs) > 1024 {
				return fail()
			}
			bindings[binding.ModelID] = true
			channels := map[string]bool{}
			for _, channel := range binding.ChannelIDs {
				if !identifier("ch", channel) || channels[channel] {
					return fail()
				}
				channels[channel] = true
			}
		}
	}
	return config, nil
}

func validCatalogReceipt(status string, code *string) bool {
	if status == "applied" {
		return code == nil
	}
	if status != "rejected" || code == nil {
		return false
	}
	switch *code {
	case "unsupported_configuration", "account_unavailable", "group_unavailable", "storage_unavailable", "apply_failed":
		return true
	}
	return false
}

func validateCatalogState(state GatewayCatalogState, gateway, instance string) bool {
	if state.GatewayID != gateway {
		return false
	}
	if state.Desired == nil {
		return state.Revision == 0 && state.InstanceID == nil && !state.Managed && state.Status == "legacy" && state.Confirmed == nil && state.LastReceipt == nil && !state.DesiredStale && !state.ConfirmedStale
	}
	if state.InstanceID == nil || *state.InstanceID != instance || state.Revision != state.Desired.Revision || state.Managed != (state.Confirmed != nil) {
		return false
	}
	if _, err := ParseGatewayCatalog(*state.Desired, gateway, instance); err != nil {
		return false
	}
	if state.Confirmed != nil {
		if _, err := ParseGatewayCatalog(*state.Confirmed, gateway, instance); err != nil || state.Confirmed.Revision > state.Revision {
			return false
		}
	} else if state.ConfirmedStale {
		return false
	}
	if state.LastReceipt != nil {
		r := state.LastReceipt
		if (CatalogStamp{r.InstanceID, r.Revision, r.Digest}) != state.Desired.Stamp() || r.ObservedAt < 1 || !validCatalogReceipt(r.Status, r.ErrorCode) {
			return false
		}
	}
	expected := "pending"
	if state.DesiredStale {
		expected = "stale"
	} else if state.Confirmed != nil && state.Confirmed.Revision == state.Revision {
		expected = "confirmed"
	} else if state.LastReceipt != nil && state.LastReceipt.Status == "rejected" {
		expected = "rejected"
	}
	return state.Status == expected
}

func (c *Client) GatewayCatalog(ctx context.Context, instance string) (GatewayCatalogState, error) {
	var result GatewayCatalogState
	if !instancePattern.MatchString(instance) {
		return result, &CallError{Code: "invalid_request"}
	}
	var raw json.RawMessage
	if err := c.call(ctx, http.MethodGet, "/gateway/v1/catalog?instance_id="+url.QueryEscape(instance), nil, nil, &raw); err != nil {
		return result, err
	}
	if decodeGatewayCatalog(raw, &result) != nil || !validateCatalogState(result, c.gatewayID, instance) {
		return GatewayCatalogState{}, &CallError{Code: "invalid_response"}
	}
	return result, nil
}

func (c *Client) ReceiveGatewayCatalog(ctx context.Context, receipt GatewayCatalogReceipt) (GatewayCatalogReceiptResult, error) {
	var result GatewayCatalogReceiptResult
	stamp := CatalogStamp{receipt.InstanceID, receipt.Revision, receipt.Digest}
	if !stamp.Valid() || !validCatalogReceipt(receipt.Status, receipt.ErrorCode) {
		return result, &CallError{Code: "invalid_request"}
	}
	var raw json.RawMessage
	if err := c.call(ctx, http.MethodPost, "/gateway/v1/catalog/receipts", nil, receipt, &raw); err != nil {
		return result, err
	}
	r := &result.Receipt
	if decodeGatewayCatalog(raw, &result) != nil || !validateCatalogState(result.State, c.gatewayID, receipt.InstanceID) || (CatalogStamp{r.InstanceID, r.Revision, r.Digest}) != stamp || r.Status != receipt.Status || !validCatalogReceipt(r.Status, r.ErrorCode) || r.ObservedAt < 1 || !reflect.DeepEqual(r.ErrorCode, receipt.ErrorCode) || result.Activated && (receipt.Status != "applied" || result.State.Confirmed == nil || result.State.Confirmed.Stamp() != stamp) {
		return GatewayCatalogReceiptResult{}, &CallError{Code: "invalid_response", Uncertain: true}
	}
	return result, nil
}
