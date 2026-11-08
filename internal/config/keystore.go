package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func KeyStorePath() (string, error) {
	dir, err := Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, ".env"), nil
}

func LoadKeyStore() (map[string]string, error) {
	p, err := KeyStorePath()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]string{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		name, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		out[strings.TrimSpace(name)] = strings.TrimSpace(value)
	}
	return out, nil
}

func SaveKeyStore(name, value string) error {
	name = strings.TrimSpace(name)
	value = strings.TrimSpace(value)
	if name == "" {
		return fmt.Errorf("key store name required")
	}
	p, err := KeyStorePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	store, err := LoadKeyStore()
	if err != nil {
		return err
	}
	store[name] = value
	names := make([]string, 0, len(store))
	for k := range store {
		names = append(names, k)
	}
	sort.Strings(names)
	var b strings.Builder
	b.WriteString("# aurora key store - managed by aurora setup\n")
	for _, k := range names {
		b.WriteString(k)
		b.WriteString("=")
		b.WriteString(store[k])
		b.WriteString("\n")
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, []byte(b.String()), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}
