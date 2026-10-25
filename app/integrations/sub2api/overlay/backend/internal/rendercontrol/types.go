// Package rendercontrol owns durable Provider intent and balance transactions.
// It is a ledger core, not yet a routed or runnable full ComfyUI Provider.
package rendercontrol

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

var (
	ErrInvalid           = errors.New("render_control_invalid")
	ErrConflict          = errors.New("render_control_conflict")
	ErrDenied            = errors.New("render_control_denied")
	ErrUnsupported       = errors.New("render_control_billing_mode_unsupported")
	ErrBalance           = errors.New("render_control_insufficient_balance")
	ErrQuota             = errors.New("render_control_key_quota")
	ErrSubscription      = errors.New("render_control_subscription_required")
	ErrSubscriptionQuota = errors.New("render_control_subscription_quota")
	ErrNotFound          = errors.New("render_control_not_found")
	ErrUncertain         = errors.New("render_control_commit_uncertain")
	ErrStorage           = errors.New("render_control_storage")
	ErrDispatched        = errors.New("render_control_dispatch_may_have_occurred")
	ErrCipher            = errors.New("render_control_request_unavailable")
	ErrFence             = errors.New("render_control_stale_effect_lease")
)

const scale int64 = 100_000_000
const maxMoney Money = 1_000_000 * Money(scale)

var moneyPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,6})(\.[0-9]{1,8})?$`)
var labelPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$`)
var instancePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{1,63}$`)
var gatewayPattern = regexp.MustCompile(`^gateway_[a-f0-9]{32}$`)
var recoveryCodePattern = regexp.MustCompile(`^[a-z0-9_]{1,64}$`)
var idPattern = regexp.MustCompile(`^(rc|rcr|rca|rcl|adm|job|evt|content|asset)_[a-f0-9]{32}$`)
var shaPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var durationPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,4})(\.[0-9]{1,3})?$`)

// Money contains units of 10^-8 USD, never binary floating point.
type Money int64

func ParseMoney(value string) (Money, error) {
	if !moneyPattern.MatchString(value) {
		return 0, ErrInvalid
	}
	parts := strings.SplitN(value, ".", 2)
	whole, _ := strconv.ParseInt(parts[0], 10, 64)
	fraction := int64(0)
	if len(parts) == 2 {
		fraction, _ = strconv.ParseInt(parts[1]+strings.Repeat("0", 8-len(parts[1])), 10, 64)
	}
	result := Money(whole*scale + fraction)
	if result > maxMoney {
		return 0, ErrInvalid
	}
	return result, nil
}

func (m Money) String() string { return fmt.Sprintf("%d.%08d", int64(m)/scale, int64(m)%scale) }

func (m Money) multiply(units int64) (Money, error) {
	if m <= 0 || m > maxMoney || units < 1 || units > 86400 || int64(m) > int64(maxMoney)/units {
		return 0, ErrInvalid
	}
	return m * Money(units), nil
}

// Subject can only be constructed from verified upstream records and trusted IP.
type Subject struct {
	instance string
	userID   int64
	keyID    int64
	ip       netip.Addr
}

func NewSubject(instance string, userID, keyID int64, trustedIP netip.Addr) (Subject, error) {
	if !instancePattern.MatchString(instance) || userID < 1 || keyID < 1 || !trustedIP.IsValid() || trustedIP.Zone() != "" {
		return Subject{}, ErrInvalid
	}
	return Subject{instance, userID, keyID, trustedIP.Unmap()}, nil
}

func (Subject) String() string   { return "rendercontrol.Subject{redacted}" }
func (Subject) GoString() string { return "rendercontrol.Subject{redacted}" }

func (s Subject) GatewaySubject() (controlclient.Subject, error) {
	return controlclient.NewSubject(s.instance, s.userID, s.keyID, s.ip)
}

type Request struct {
	catalog        *controlclient.CatalogStamp
	usageAccountID int64
	operation      string
	taskType       string
	model          string
	inputHash      string
	body           []byte
	hash           string
	units          int64
	requireAssets  bool
	videoMillis    int64
	videoSize      string
}

func (Request) String() string   { return "rendercontrol.Request{redacted}" }
func (Request) GoString() string { return "rendercontrol.Request{redacted}" }

// NewRequest freezes the normalized, already validated gateway request. The
// public parser must also enforce the shared job schema and media policy.
func NewRequest(operation string, request controlclient.JobRequest) (Request, error) {
	image := request.TaskType == "text_to_image" || request.TaskType == "image_to_image" || request.TaskType == "references_to_image"
	video := request.TaskType == "text_to_video" || request.TaskType == "references_to_video"
	audio := request.TaskType == "text_to_audio"
	if !image && !video && !audio || audio && operation != "audio.create" || image && operation != "images.generate" && operation != "images.edit" || video && operation != "videos.create" || !utf8.ValidString(request.Model) || utf8.RuneCountInString(request.Model) < 1 || utf8.RuneCountInString(request.Model) > 128 {
		return Request{}, ErrInvalid
	}
	body, err := json.Marshal(request)
	if err != nil || len(body) > 2<<20 {
		return Request{}, ErrInvalid
	}
	// Decode with numbers intact and re-encode to make raw JSON/map ordering
	// independent of the caller while rejecting duplicate keys and deep graphs.
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var tokens int
	if err = validateJSON(decoder, 0, &tokens); err != nil {
		return Request{}, ErrInvalid
	}
	decoder = json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var canonical map[string]any
	if decoder.Decode(&canonical) != nil {
		return Request{}, ErrInvalid
	}
	body, err = json.Marshal(canonical)
	if err != nil {
		return Request{}, ErrInvalid
	}
	parameters, _ := canonical["parameters"].(map[string]any)
	units := int64(1)
	var videoMillis int64
	var videoSize string
	if image {
		if value, exists := parameters["n"]; exists {
			number, ok := value.(json.Number)
			if !ok {
				return Request{}, ErrInvalid
			}
			units, err = number.Int64()
			if err != nil || units < 1 || units > 100 {
				return Request{}, ErrInvalid
			}
		}
	} else {
		number, ok := parameters["seconds"].(json.Number)
		if !ok || !durationPattern.MatchString(string(number)) {
			return Request{}, ErrInvalid
		}
		parts := strings.SplitN(string(number), ".", 2)
		seconds, _ := strconv.ParseInt(parts[0], 10, 64)
		milliseconds := int64(0)
		if len(parts) == 2 {
			milliseconds, _ = strconv.ParseInt(parts[1]+strings.Repeat("0", 3-len(parts[1])), 10, 64)
		}
		duration := seconds*1000 + milliseconds
		if duration < 1 || duration > 86_400_000 || audio && duration > 600_000 {
			return Request{}, ErrInvalid
		}
		units = (duration + 999) / 1000
		if video {
			videoMillis = duration
			if value, present := parameters["size"]; present {
				var valid bool
				videoSize, valid = value.(string)
				if !valid || !publicSizePattern.MatchString(videoSize) {
					return Request{}, ErrInvalid
				}
			}
		}
	}
	return Request{operation: operation, taskType: request.TaskType, model: request.Model, inputHash: digest(body), body: body, hash: digest(body), units: units, videoMillis: videoMillis, videoSize: videoSize}, nil
}

func validateJSON(decoder *json.Decoder, depth int, count *int) error {
	*count++
	if depth > 32 || *count > 10000 {
		return ErrInvalid
	}
	token, err := decoder.Token()
	if err != nil {
		return ErrInvalid
	}
	delimiter, compound := token.(json.Delim)
	if !compound {
		return nil
	}
	if delimiter != '{' && delimiter != '[' {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for decoder.More() {
		if delimiter == '{' {
			key, keyErr := decoder.Token()
			name, ok := key.(string)
			if keyErr != nil || !ok || seen[name] {
				return ErrInvalid
			}
			seen[name] = true
		}
		if err := validateJSON(decoder, depth+1, count); err != nil {
			return err
		}
	}
	_, err = decoder.Token()
	return err
}

type Quote struct {
	Version   string
	UnitPrice Money
	MaxUnits  int64
}

// Target is supplied by trusted Provider configuration, never a customer body.
type Target struct{ gatewayID string }

func NewTarget(gatewayID string) (Target, error) {
	if !gatewayPattern.MatchString(gatewayID) {
		return Target{}, ErrInvalid
	}
	return Target{gatewayID: gatewayID}, nil
}

// Intent is a metadata-only projection; ciphertext and plaintext stay private.
type Intent struct {
	Catalog                    *controlclient.CatalogStamp
	ID                         string
	UsageAccountID             int64
	BillingGroupID             int64
	RateMultiplier             Rate
	RateSource                 string
	BillingType                int
	SubscriptionID             int64
	Model                      string
	CreatedAt                  time.Time
	ClientRequestHash          string
	Instance                   string
	TargetGatewayID            string
	UserID                     int64
	KeyID                      int64
	TrustedIP                  netip.Addr
	Operation                  string
	RequestHash                string
	TaskType                   string
	ProviderRequestID          string
	AuthorizationRef           string
	PriceVersion               string
	UnitPrice                  Money
	MaxUnits                   int64
	HoldAmount                 Money
	State                      string
	DispatchCount              int
	AdmissionID                string
	RemoteJobID                string
	TerminalEventID            string
	TerminalHash               string
	Outcome                    string
	ActualAmount               Money
	ImageCount                 int64
	VideoMilliseconds          int64
	AudioMilliseconds          int64
	LedgerRef                  string
	RequestedVideoMilliseconds int64
	VideoSize                  string
}

func (i Intent) Terminal() bool { return i.State == "settled" || i.State == "released" }

// RequestIdentity reconstructs only immutable references, without decryption or
// current customer authority. Legacy intents with unknown targets fail closed.
func (i Intent) RequestIdentity() (controlclient.RequestIdentity, error) {
	subject, err := controlclient.NewSubject(i.Instance, i.UserID, i.KeyID, i.TrustedIP)
	if err != nil {
		return controlclient.RequestIdentity{}, ErrInvalid
	}
	identity, err := controlclient.NewRequestIdentity(i.TargetGatewayID, subject, controlclient.AdmissionRequest{
		ProviderRequestID: i.ProviderRequestID, ExternalTaskRef: i.ID, PriceVersion: i.PriceVersion, AuthorizationRef: i.AuthorizationRef,
	})
	if err != nil {
		return controlclient.RequestIdentity{}, ErrInvalid
	}
	return identity, nil
}

type vault struct {
	active string
	keys   map[string]cipher.AEAD
}

func newVault(active string, keys map[string][]byte) (vault, error) {
	if !labelPattern.MatchString(active) || len(keys) == 0 || len(keys) > 4 {
		return vault{}, ErrInvalid
	}
	result := vault{active: active, keys: make(map[string]cipher.AEAD, len(keys))}
	for id, key := range keys {
		if !labelPattern.MatchString(id) || len(key) != 32 {
			return vault{}, ErrInvalid
		}
		block, err := aes.NewCipher(bytes.Clone(key))
		if err != nil {
			return vault{}, ErrInvalid
		}
		aead, err := cipher.NewGCM(block)
		if err != nil {
			return vault{}, ErrInvalid
		}
		result.keys[id] = aead
	}
	if result.keys[active] == nil {
		return vault{}, ErrInvalid
	}
	return result, nil
}

func aad(i Intent) []byte {
	return []byte(fmt.Sprintf("render-control-v1\n%s\n%d\n%d\n%s\n%s", i.Instance, i.UserID, i.KeyID, i.ID, i.RequestHash))
}

func (v vault) seal(i Intent, plaintext []byte) ([]byte, error) {
	aead := v.keys[v.active]
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, ErrCipher
	}
	return aead.Seal(nonce, nonce, plaintext, aad(i)), nil
}

func (v vault) open(i Intent, keyID string, ciphertext []byte) ([]byte, error) {
	aead := v.keys[keyID]
	if aead == nil || len(ciphertext) < aead.NonceSize()+aead.Overhead() {
		return nil, ErrCipher
	}
	plaintext, err := aead.Open(nil, ciphertext[:aead.NonceSize()], ciphertext[aead.NonceSize():], aad(i))
	if err != nil || digest(plaintext) != i.RequestHash {
		return nil, ErrCipher
	}
	return plaintext, nil
}

func digest(value []byte) string { sum := sha256.Sum256(value); return hex.EncodeToString(sum[:]) }

func newID(prefix string) (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", ErrStorage
	}
	return prefix + "_" + hex.EncodeToString(value), nil
}

func validID(prefix, value string) bool {
	return strings.HasPrefix(value, prefix+"_") && idPattern.MatchString(value)
}
