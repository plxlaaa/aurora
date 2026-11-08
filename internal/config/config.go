package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

type Config struct {
	LLM    LLMConfig    `json:"llm"`
	Update UpdateConfig `json:"update"`
	Agent  AgentConfig  `json:"agent"`
}

type LLMConfig struct {
	Provider  string `json:"provider"`
	BaseURL   string `json:"base_url"`
	Model     string `json:"model"`
	APIKeyEnv string `json:"api_key_env"`
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
			Provider:  "openai",
			BaseURL:   "https://api.openai.com/v1",
			Model:     "gpt-4o-mini",
			APIKeyEnv: "AURORA_API_KEY",
		},
		Update: UpdateConfig{
			Enabled:     true,
			ChannelRace: true,
			TimeoutMS:   20000,
			Repo:        "plxlaaa/aurora",
			Mirrors: []string{
				"github",
				"ghproxy",
				"ghproxy_net",
				"gitdl",
				"gh_ddlc",
			},
			ManifestURLs: DefaultManifestURLs("plxlaaa/aurora"),
		},
		Agent: AgentConfig{
			MaxSteps:     32,
			Shell:        "auto",
			ConfirmShell: true,
		},
	}
}

func DefaultManifestURLs(repo string) []string {
	base := "https://github.com/" + repo + "/releases/latest/download/manifest.json"
	return []string{
		base,
		"https://ghproxy.net/" + base,
		"https://mirror.ghproxy.com/" + base,
		"https://gitdl.cn/" + base,
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
		cfg.LLM.APIKeyEnv = "AURORA_API_KEY"
	}
	if cfg.Update.TimeoutMS <= 0 {
		cfg.Update.TimeoutMS = 20000
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
		env = "AURORA_API_KEY"
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
