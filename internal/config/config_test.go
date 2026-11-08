package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultManifestURLs(t *testing.T) {
	urls := DefaultManifestURLs("plxlaaa/aurora")
	if len(urls) < 3 {
		t.Fatal("expected mirrored manifest urls")
	}
}

func TestSaveLoad(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("APPDATA", dir)
	t.Setenv("HOME", dir)
	t.Setenv("XDG_CONFIG_HOME", dir)
	cfg := Default()
	cfg.LLM.Model = "demo-model"
	if err := Save(cfg); err != nil {
		p, _ := Path()
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := Save(cfg); err != nil {
			t.Fatal(err)
		}
	}
	got, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.LLM.Model != "demo-model" && got.LLM.Model == "" {
		t.Fatalf("unexpected model %q", got.LLM.Model)
	}
}

func TestSetField(t *testing.T) {
	cfg := Default()
	if err := SetField(&cfg, "model", "kimi"); err != nil {
		t.Fatal(err)
	}
	if cfg.LLM.Model != "kimi" {
		t.Fatalf("model=%s", cfg.LLM.Model)
	}
	if err := SetField(&cfg, "max_steps", "12"); err != nil {
		t.Fatal(err)
	}
	if cfg.Agent.MaxSteps != 12 {
		t.Fatalf("max_steps=%d", cfg.Agent.MaxSteps)
	}
	if err := SetField(&cfg, "nope", "x"); err == nil {
		t.Fatal("expected unknown key error")
	}
}
