package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

type Config struct {
	LLM    LLMConfig    `json:"llm"`
	Update UpdateConfig `json:"update"`
	Agent  AgentConfig  `json:"agent"`
}

type LLMConfig struct {
	Provider      string `json:"provider"`
	BaseURL       string `json:"base_url"`
	Model         string `json:"model"`
	APIKeyEnv     string `json:"api_key_env"`
	ContextWindow int64  `json:"context_window"`
}

type UpdateConfig struct {
	Enabled      bool     `json:"enabled"`
	ChannelRace  bool     `json:"channel_race"`
	TimeoutMS    int      `json:"timeout_ms"`
	Repo         string   `json:"repo"`
	Mirrors      []string `json:"mirrors"`
	ManifestURLs []string `json:"manifest_urls"`
}

type AgentConfig struct {
	MaxSteps     int    `json:"max_steps"`
	Shell        string `json:"shell"`
	Workspace    string `json:"workspace"`
	ConfirmShell bool   `json:"confirm_shell"`
}

func Default() Config {
	return Config{
		LLM: LLMConfig{
			Provider:      "openai",
			BaseURL:       "https://api.openai.com/v1",
			Model:         "gpt-4o-mini",
			APIKeyEnv:     "AURORA_API_KEY",
			ContextWindow: 128000,
		},
		Update: UpdateConfig{
			Enabled:      true,
			ChannelRace:  true,
			TimeoutMS:    60000,
			Repo:         "plxlaaa/aurora",
			Mirrors:      DefaultMirrors(),
			ManifestURLs: DefaultManifestURLs("plxlaaa/aurora"),
		},
		Agent: AgentConfig{
			MaxSteps:     32,
			Shell:        "auto",
			ConfirmShell: true,
		},
	}
}

const DefaultKeyEnv = "AURORA_API_KEY"

const retiredDefaultTimeoutMS = 20000

var retiredMirrors = map[string]bool{
	"gitdl":      true,
	"ghproxy":    true,
	"ghproxy_cc": true,
}

var retiredManifestPrefixes = []string{
	"gitdl.cn",
	"mirror.ghproxy.com",
}

func hasRetiredManifestURL(urls []string) bool {
	for _, u := range urls {
		for _, dead := range retiredManifestPrefixes {
			if strings.Contains(u, dead) {
				return true
			}
		}
	}
	return false
}

func DefaultMirrors() []string {
	return []string{
		"github",
		"gh_proxy_com",
		"ghfast_top",
		"ghproxy_net",
		"gh_ddlc",
	}
}

func healMirrors(mirrors []string) []string {
	out := sanitizeMirrors(mirrors)
	if len(out) == 0 {
		return DefaultMirrors()
	}
	seen := map[string]bool{}
	for _, m := range out {
		seen[m] = true
	}
	for _, m := range DefaultMirrors() {
		if !seen[m] {
			out = append(out, m)
		}
	}
	return out
}

func sanitizeMirrors(mirrors []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(mirrors))
	for _, m := range mirrors {
		m = strings.ToLower(strings.TrimSpace(m))
		if m == "" || retiredMirrors[m] || seen[m] {
			continue
		}
		seen[m] = true
		out = append(out, m)
	}
	return out
}

func DefaultManifestURLs(repo string) []string {
	base := "https://github.com/" + repo + "/releases/latest/download/manifest.json"
	return []string{
		base,
		"https://gh-proxy.com/" + base,
		"https://ghfast.top/" + base,
		"https://ghproxy.net/" + base,
		"https://gh.ddlc.top/" + base,
	}
}

func Dir() (string, error) {
	if runtime.GOOS == "windows" {
		base := os.Getenv("APPDATA")
		if base == "" {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", err
			}
			base = filepath.Join(home, "AppData", "Roaming")
		}
		return filepath.Join(base, "aurora"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".config", "aurora"), nil
}

func Path() (string, error) {
	dir, err := Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "config.json"), nil
}

func Exists() (bool, error) {
	p, err := Path()
	if err != nil {
		return false, err
	}
	_, err = os.Stat(p)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return false, err
}

func InstallDir() (string, error) {
	if runtime.GOOS == "windows" {
		base := os.Getenv("LOCALAPPDATA")
		if base == "" {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", err
			}
			base = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(base, "Programs", "Aurora"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "bin"), nil
}

func Load() (Config, error) {
	cfg := Default()
	p, err := Path()
	if err != nil {
		return cfg, err
	}
	raw, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return cfg, nil
	}
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("parse config: %w", err)
	}
	if cfg.LLM.APIKeyEnv == "" {
		cfg.LLM.APIKeyEnv = DefaultKeyEnv
	}
	if cfg.Update.TimeoutMS <= 0 || cfg.Update.TimeoutMS == retiredDefaultTimeoutMS {
		cfg.Update.TimeoutMS = 60000
	}
	cfg.Update.Mirrors = healMirrors(cfg.Update.Mirrors)
	if len(cfg.Update.ManifestURLs) == 0 || hasRetiredManifestURL(cfg.Update.ManifestURLs) {
		cfg.Update.ManifestURLs = DefaultManifestURLs(cfg.Update.Repo)
	}
	if cfg.Agent.MaxSteps <= 0 {
		cfg.Agent.MaxSteps = 32
	}
	return cfg, nil
}

func Save(cfg Config) error {
	dir, err := Dir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	p, err := Path()
	if err != nil {
		return err
	}
	raw, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

func APIKey(cfg Config) string {
	env := strings.TrimSpace(cfg.LLM.APIKeyEnv)
	if env == "" {
		env = DefaultKeyEnv
	}
	if store, err := LoadKeyStore(); err == nil {
		if v := strings.TrimSpace(store[env]); v != "" {
			return v
		}
	}
	if v := strings.TrimSpace(os.Getenv(env)); v != "" {
		return v
	}
	return strings.TrimSpace(os.Getenv("OPENAI_API_KEY"))
}

func SetField(cfg *Config, key, value string) error {
	key = strings.ToLower(strings.TrimSpace(key))
	value = strings.TrimSpace(value)
	switch key {
	case "llm.model", "model":
		cfg.LLM.Model = value
	case "llm.base_url", "base_url":
		cfg.LLM.BaseURL = strings.TrimRight(value, "/")
	case "llm.provider", "provider":
		cfg.LLM.Provider = value
	case "llm.api_key_env", "api_key_env":
		cfg.LLM.APIKeyEnv = value
	case "llm.context_window", "context_window":
		if strings.HasPrefix(value, "-") {
			return fmt.Errorf("context_window must be a positive integer")
		}
		n := int64(0)
		for _, ch := range value {
			if ch < '0' || ch > '9' {
				return fmt.Errorf("context_window must be a positive integer")
			}
			n = n*10 + int64(ch-'0')
		}
		if n <= 0 {
			return fmt.Errorf("context_window must be a positive integer")
		}
		cfg.LLM.ContextWindow = n
	case "agent.max_steps", "max_steps":
		n := 0
		for _, ch := range value {
			if ch < '0' || ch > '9' {
				return fmt.Errorf("max_steps must be an integer")
			}
			n = n*10 + int(ch-'0')
		}
		if n <= 0 {
			return fmt.Errorf("max_steps must be > 0")
		}
		cfg.Agent.MaxSteps = n
	case "agent.confirm_shell", "confirm_shell":
		cfg.Agent.ConfirmShell = value == "1" || strings.EqualFold(value, "true") || strings.EqualFold(value, "yes")
	case "update.repo", "repo":
		cfg.Update.Repo = value
	case "update.timeout_ms", "timeout_ms":
		n, err := strconv.Atoi(value)
		if err != nil || n <= 0 {
			return fmt.Errorf("timeout_ms must be a positive integer")
		}
		cfg.Update.TimeoutMS = n
	case "update.mirrors", "mirrors":
		cfg.Update.Mirrors = sanitizeMirrors(strings.FieldsFunc(value, func(r rune) bool {
			return r == ',' || r == ' ' || r == '\t' || r == ';'
		}))
		if len(cfg.Update.Mirrors) == 0 {
			return fmt.Errorf("mirrors must name at least one channel")
		}
	default:
		return fmt.Errorf("unknown config key %s", key)
	}
	return nil
}

func Ensure() (Config, error) {
	cfg, err := Load()
	if err != nil {
		return cfg, err
	}
	p, err := Path()
	if err != nil {
		return cfg, err
	}
	if _, err := os.Stat(p); errors.Is(err, os.ErrNotExist) {
		if err := Save(cfg); err != nil {
			return cfg, err
		}
	}
	return cfg, nil
}
