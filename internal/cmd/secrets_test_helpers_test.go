package cmd

import (
	"errors"
	"sort"
	"strings"

	"github.com/99designs/keyring"

	"github.com/openclaw/gogcli/internal/config"
	"github.com/openclaw/gogcli/internal/secrets"
)

type memSecretsStore struct {
	tokens   map[string]secrets.Token
	defaults map[string]string
	secrets  map[string][]byte
}

func newMemSecretsStore() *memSecretsStore {
	return &memSecretsStore{
		tokens:   make(map[string]secrets.Token),
		defaults: make(map[string]string),
		secrets:  make(map[string][]byte),
	}
}

func normalizeEmailTest(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

func (s *memSecretsStore) Keys() ([]string, error) {
	keys := make([]string, 0, len(s.tokens))
	for key := range s.tokens {
		parts := strings.SplitN(key, ":", 2)
		if len(parts) != 2 {
			continue
		}
		keys = append(keys, secrets.TokenKey(parts[0], parts[1]))
	}
	sort.Strings(keys)
	return keys, nil
}

func (s *memSecretsStore) SetToken(client string, email string, tok secrets.Token) error {
	email = normalizeEmailTest(email)
	if email == "" {
		return errors.New("missing email")
	}
	if strings.TrimSpace(tok.RefreshToken) == "" {
		return errors.New("missing refresh token")
	}
	if client == "" {
		client = config.DefaultClientName
	}
	tok.Email = email
	tok.Client = client
	s.tokens[client+":"+email] = tok
	return nil
}

func (s *memSecretsStore) GetToken(client string, email string) (secrets.Token, error) {
	email = normalizeEmailTest(email)
	if email == "" {
		return secrets.Token{}, errors.New("missing email")
	}
	if client == "" {
		client = config.DefaultClientName
	}
	if tok, ok := s.tokens[client+":"+email]; ok {
		return tok, nil
	}
	return secrets.Token{}, keyring.ErrKeyNotFound
}

func (s *memSecretsStore) DeleteToken(client string, email string) error {
	email = normalizeEmailTest(email)
	if email == "" {
		return errors.New("missing email")
	}
	if client == "" {
		client = config.DefaultClientName
	}
	if _, ok := s.tokens[client+":"+email]; !ok {
		return keyring.ErrKeyNotFound
	}
	delete(s.tokens, client+":"+email)
	return nil
}

func (s *memSecretsStore) ListTokens() ([]secrets.Token, error) {
	out := make([]secrets.Token, 0, len(s.tokens))
	for _, t := range s.tokens {
		out = append(out, t)
	}
	return out, nil
}

func (s *memSecretsStore) GetDefaultAccount(client string) (string, error) {
	if client == "" {
		client = config.DefaultClientName
	}
	return s.defaults[client], nil
}

func (s *memSecretsStore) SetDefaultAccount(client string, email string) error {
	if client == "" {
		client = config.DefaultClientName
	}
	s.defaults[client] = email
	return nil
}

func (s *memSecretsStore) SetSecret(key string, value []byte) error {
	s.secrets[key] = append([]byte(nil), value...)
	return nil
}

func (s *memSecretsStore) GetSecret(key string) ([]byte, error) {
	value, ok := s.secrets[key]
	if !ok {
		return nil, keyring.ErrKeyNotFound
	}
	return append([]byte(nil), value...), nil
}

func (s *memSecretsStore) DeleteSecret(key string) error {
	if _, ok := s.secrets[key]; !ok {
		return keyring.ErrKeyNotFound
	}
	delete(s.secrets, key)
	return nil
}
