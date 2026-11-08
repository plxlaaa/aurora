package config

import "testing"

func TestProvidersUniqueIDs(t *testing.T) {
	seen := map[string]bool{}
	for _, p := range Providers() {
		if seen[p.ID] {
			t.Fatalf("duplicate provider id %q", p.ID)
		}
		seen[p.ID] = true
		if p.ID != "custom" {
			if p.BaseURL == "" || p.DefaultModel == "" {
				t.Fatalf("preset %q needs base URL and default model", p.ID)
			}
		}
	}
	if _, ok := FindProvider("custom"); !ok {
		t.Fatal("custom preset missing")
	}
	if _, ok := FindProvider("nope"); ok {
		t.Fatal("unknown provider should not resolve")
	}
	if p, _ := FindProvider("DeepSeek"); p.ID != "deepseek" {
		t.Fatalf("case-insensitive lookup failed: %q", p.ID)
	}
}
