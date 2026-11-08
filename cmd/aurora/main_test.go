package main

import (
	"bufio"
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/config"
)

func TestOfferFirstRunSetupSkippedWhenConfigExists(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("APPDATA", dir)
	t.Setenv("AURORA_API_KEY", "")
	t.Setenv("OPENAI_API_KEY", "")
	if err := config.Save(config.Default()); err != nil {
		t.Fatal(err)
	}
	cfg := config.Default()
	got, err := offerFirstRunSetup(cfg, bufio.NewReader(strings.NewReader("y\n")))
	if err != nil {
		t.Fatal(err)
	}
	if got.LLM.Model != cfg.LLM.Model {
		t.Fatal("config should be untouched when a config file exists")
	}
}

func TestOfferFirstRunSetupSkippedWhenEnvKeySet(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("APPDATA", dir)
	t.Setenv("AURORA_API_KEY", "sk-env")
	cfg := config.Default()
	got, err := offerFirstRunSetup(cfg, bufio.NewReader(strings.NewReader("y\n")))
	if err != nil {
		t.Fatal(err)
	}
	if got.LLM.Provider != cfg.LLM.Provider {
		t.Fatal("config should be untouched when an environment key is set")
	}
}
