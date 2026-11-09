package config

import (
	"os"
	"strings"
	"testing"
)

func TestHealMirrorsRemovesRetiredAndBackfills(t *testing.T) {
	got := healMirrors([]string{"github", "gitdl", "ghproxy", "gh_ddlc"})
	joined := "," + strings.Join(got, ",") + ","
	if strings.Contains(joined, ",gitdl,") || strings.Contains(joined, ",ghproxy,") {
		t.Fatalf("retired mirrors survived: %v", got)
	}
	seen := map[string]bool{}
	for _, m := range got {
		if seen[m] {
			t.Fatalf("duplicate mirror %q in %v", m, got)
		}
		seen[m] = true
	}
	for _, want := range DefaultMirrors() {
		if !seen[want] {
			t.Fatalf("healthy default %q missing after heal: %v", want, got)
		}
	}
}

func TestHealMirrorsEmptyKeepsDefaults(t *testing.T) {
	got := healMirrors(nil)
	if len(got) != len(DefaultMirrors()) {
		t.Fatalf("got %v", got)
	}
}

func TestSanitizeMirrorsKeepsExplicitList(t *testing.T) {
	got := sanitizeMirrors([]string{"GitHub", "ghfast_top", "ghfast_top", "gitdl"})
	if len(got) != 2 || got[0] != "github" || got[1] != "ghfast_top" {
		t.Fatalf("got %v", got)
	}
}

func TestDefaultManifestURLsExcludesDeadMirrors(t *testing.T) {
	urls := DefaultManifestURLs("plxlaaa/aurora")
	joined := strings.Join(urls, " ")
	for _, dead := range []string{"gitdl.cn", "mirror.ghproxy.com"} {
		if strings.Contains(joined, dead) {
			t.Fatalf("dead prefix %q in %v", dead, urls)
		}
	}
	if urls[0] == "" {
		t.Fatal("direct GitHub URL must come first")
	}
}

func TestLoadHealsConfigFromOldVersion(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("APPDATA", dir)
	if err := os.MkdirAll(dir+"/.config/aurora", 0o755); err != nil {
		t.Fatal(err)
	}
	legacy := `{
	  "llm": {},
	  "update": {
	    "enabled": true,
	    "channel_race": true,
	    "timeout_ms": 20000,
	    "repo": "plxlaaa/aurora",
	    "mirrors": ["github", "ghproxy", "ghproxy_net", "gitdl", "gh_ddlc"],
	    "manifest_urls": ["https://gitdl.cn/https://github.com/plxlaaa/aurora/releases/latest/download/manifest.json"]
	  },
	  "agent": {"max_steps": 10}
	}`
	p, err := Path()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(legacy), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range cfg.Update.Mirrors {
		if retiredMirrors[m] {
			t.Fatalf("retired mirror %q survived load: %v", m, cfg.Update.Mirrors)
		}
	}
	if len(cfg.Update.Mirrors) < len(DefaultMirrors()) {
		t.Fatalf("defaults not backfilled: %v", cfg.Update.Mirrors)
	}
	for _, u := range cfg.Update.ManifestURLs {
		if strings.Contains(u, "gitdl.cn") || strings.Contains(u, "mirror.ghproxy.com") {
			t.Fatalf("dead manifest URL survived load: %v", cfg.Update.ManifestURLs)
		}
	}
	if cfg.Update.TimeoutMS != 60000 {
		t.Fatalf("timeout = %d, want migrated 60000", cfg.Update.TimeoutMS)
	}
	if cfg.Agent.MaxSteps != 10 {
		t.Fatalf("explicit max_steps should survive: %d", cfg.Agent.MaxSteps)
	}
}

func TestSetFieldMirrorsAndTimeout(t *testing.T) {
	cfg := Default()
	if err := SetField(&cfg, "update.mirrors", "github, gh_proxy_com, gitdl"); err != nil {
		t.Fatal(err)
	}
	for _, m := range cfg.Update.Mirrors {
		if retiredMirrors[m] {
			t.Fatalf("retired mirror accepted: %v", cfg.Update.Mirrors)
		}
	}
	if err := SetField(&cfg, "update.timeout_ms", "90000"); err != nil {
		t.Fatal(err)
	}
	if cfg.Update.TimeoutMS != 90000 {
		t.Fatalf("timeout = %d", cfg.Update.TimeoutMS)
	}
	if err := SetField(&cfg, "update.timeout_ms", "abc"); err == nil {
		t.Fatal("non-numeric timeout should fail")
	}
	if err := SetField(&cfg, "update.mirrors", " , "); err == nil {
		t.Fatal("empty mirror list should fail")
	}
}
