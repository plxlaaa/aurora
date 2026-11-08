package config

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func tempConfigDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("APPDATA", dir)
	return dir
}

func TestKeyStoreRoundTrip(t *testing.T) {
	tempConfigDir(t)
	if err := SaveKeyStore("AURORA_API_KEY", "sk-abc"); err != nil {
		t.Fatal(err)
	}
	if err := SaveKeyStore("OTHER_KEY", "other"); err != nil {
		t.Fatal(err)
	}
	store, err := LoadKeyStore()
	if err != nil {
		t.Fatal(err)
	}
	if store["AURORA_API_KEY"] != "sk-abc" || store["OTHER_KEY"] != "other" {
		t.Fatalf("upsert dropped entries: %v", store)
	}
}

func TestKeyStoreIgnoresCommentsAndBlanks(t *testing.T) {
	tempConfigDir(t)
	p, err := KeyStorePath()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	raw := "# comment\n\nAURORA_API_KEY=sk-x\nBROKEN LINE\n"
	if err := os.WriteFile(p, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := LoadKeyStore()
	if err != nil {
		t.Fatal(err)
	}
	if len(store) != 1 || store["AURORA_API_KEY"] != "sk-x" {
		t.Fatalf("unexpected parse: %v", store)
	}
}

func TestKeyStorePermissions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("posix permissions unavailable")
	}
	tempConfigDir(t)
	if err := SaveKeyStore("AURORA_API_KEY", "sk-abc"); err != nil {
		t.Fatal(err)
	}
	p, _ := KeyStorePath()
	st, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	if perm := st.Mode().Perm(); perm != 0o600 {
		t.Fatalf("key store perm = %v, want 0600", perm)
	}
}

func TestKeyStoreMissingFile(t *testing.T) {
	tempConfigDir(t)
	store, err := LoadKeyStore()
	if err != nil {
		t.Fatal(err)
	}
	if len(store) != 0 {
		t.Fatalf("expected empty store, got %v", store)
	}
}

func TestAPIKeyPrecedence(t *testing.T) {
	tempConfigDir(t)
	t.Setenv("AURORA_API_KEY", "")
	t.Setenv("OPENAI_API_KEY", "")
	cfg := Default()

	if got := APIKey(cfg); got != "" {
		t.Fatalf("expected empty key, got %q", got)
	}
	t.Setenv("AURORA_API_KEY", "env-key")
	if got := APIKey(cfg); got != "env-key" {
		t.Fatalf("env fallback failed: %q", got)
	}
	if err := SaveKeyStore("AURORA_API_KEY", "store-key"); err != nil {
		t.Fatal(err)
	}
	if got := APIKey(cfg); got != "store-key" {
		t.Fatalf("key store should win over env: %q", got)
	}
}

func TestExists(t *testing.T) {
	tempConfigDir(t)
	exists, err := Exists()
	if err != nil {
		t.Fatal(err)
	}
	if exists {
		t.Fatal("config should not exist in fresh dir")
	}
	if err := Save(Default()); err != nil {
		t.Fatal(err)
	}
	exists, err = Exists()
	if err != nil {
		t.Fatal(err)
	}
	if !exists {
		t.Fatal("config should exist after Save")
	}
}
