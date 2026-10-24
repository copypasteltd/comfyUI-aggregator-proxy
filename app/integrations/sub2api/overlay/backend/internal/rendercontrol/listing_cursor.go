package rendercontrol

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

var errCursorInvalid = errors.New("invalid collection cursor")
var errCursorExpired = errors.New("expired collection cursor")
var errListCapacity = errors.New("collection capacity")

type listingPosition struct {
	Version int       `json:"v"`
	Issued  int64     `json:"issued"`
	Through time.Time `json:"through"`
	After   time.Time `json:"after"`
	ID      string    `json:"id"`
	Stream  int       `json:"stream"`
	Remote  string    `json:"remote"`
}

func listingAAD(subject Subject, purpose, scope string) []byte {
	return []byte(fmt.Sprintf("render-control-collection-v1\n%s\n%d\n%d\n%s\n%s", subject.instance, subject.userID, subject.keyID, purpose, scope))
}

func (s *Store) readListingCursor(subject Subject, purpose, scope, token string, now time.Time) (listingPosition, error) {
	if token == "" {
		return listingPosition{Version: 1, Issued: now.Unix(), Through: now.UTC()}, nil
	}
	if len(token) > 8192 {
		return listingPosition{}, errCursorInvalid
	}
	key, encoded, found := strings.Cut(token, ".")
	if !found || s.vault.keys[key] == nil {
		return listingPosition{}, errCursorInvalid
	}
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	aead := s.vault.keys[key]
	if err != nil || len(raw) < aead.NonceSize()+aead.Overhead() {
		return listingPosition{}, errCursorInvalid
	}
	plaintext, err := aead.Open(nil, raw[:aead.NonceSize()], raw[aead.NonceSize():], listingAAD(subject, purpose, scope))
	var position listingPosition
	if err != nil || len(plaintext) > 6000 || json.Unmarshal(plaintext, &position) != nil || position.Version != 1 || position.Issued < 1 || position.Issued > now.Unix()+60 || position.Through.IsZero() || position.Through.Unix() > position.Issued+1 || position.Stream < 0 || position.Stream > 256 || len(position.Remote) > 4096 || position.Remote != "" && !asciiPublic(position.Remote, 1, 4096) || position.ID != "" && (!validID("rc", position.ID) || position.After.IsZero() || position.After.After(position.Through)) {
		return listingPosition{}, errCursorInvalid
	}
	if now.Unix()-position.Issued > 900 {
		return listingPosition{}, errCursorExpired
	}
	return position, nil
}

func (s *Store) writeListingCursor(subject Subject, purpose, scope string, position listingPosition) (*string, error) {
	plaintext, err := json.Marshal(position)
	if err != nil || len(plaintext) > 6000 {
		return nil, ErrStorage
	}
	aead := s.vault.keys[s.vault.active]
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, ErrStorage
	}
	raw := aead.Seal(nonce, nonce, plaintext, listingAAD(subject, purpose, scope))
	token := s.vault.active + "." + base64.RawURLEncoding.EncodeToString(raw)
	if len(token) > 8192 {
		return nil, ErrStorage
	}
	return &token, nil
}
