package agent

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/plxlaaa/aurora/internal/config"
	"github.com/plxlaaa/aurora/internal/llm"
)

// Session persistence: conversation memory survives terminal restarts,
// modeled on Codex's resume picker and Claude Code's --continue.
// Files live under <config dir>/sessions/<slug>.json.

type SavedSession struct {
	Workspace string       `json:"workspace"`
	Model     string       `json:"model"`
	SavedAt   string       `json:"saved_at"`
	Messages  []llm.Message `json:"messages"`
	Usage     llm.Usage    `json:"usage"`
}

func sessionsDir() (string, error) {
	dir, err := config.Dir()
	if err != nil {
		return "", err
	}
	p := filepath.Join(dir, "sessions")
	if err := os.MkdirAll(p, 0o700); err != nil {
		return "", err
	}
	return p, nil
}

// sessionSlug turns the workspace path into a stable file name.
func sessionSlug(workspace string) string {
	base := strings.ToLower(filepath.Base(filepath.Clean(workspace)))
	var b strings.Builder
	for _, r := range base {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			b.WriteRune(r)
		} else {
			b.WriteRune('-')
		}
	}
	s := b.String()
	if s == "" {
		s = "workspace"
	}
	// short hash suffix so two dirs with the same basename don't collide
	var h uint64
	for _, c := range workspace {
		h = h*31 + uint64(c)
	}
	return fmt.Sprintf("%s-%04x", s, h&0xffff)
}

// SaveSession snapshots the conversation for later resume.
func SaveSession(cfg config.Config, workspace string, r *Runner) error {
	dir, err := sessionsDir()
	if err != nil {
		return err
	}
	snap := SavedSession{
		Workspace: workspace,
		Model:     r.Model(),
		SavedAt:   time.Now().UTC().Format(time.RFC3339),
		Messages:  r.Snapshot(),
		Usage:     r.SessionUsage(),
	}
	raw, err := json.MarshalIndent(snap, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, sessionSlug(workspace)+".json"), raw, 0o600)
}

// LoadSession restores a saved conversation for the given workspace.
func LoadSession(cfg config.Config, workspace string, r *Runner) (bool, error) {
	dir, err := sessionsDir()
	if err != nil {
		return false, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, sessionSlug(workspace)+".json"))
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	var snap SavedSession
	if err := json.Unmarshal(raw, &snap); err != nil {
		return false, err // corrupted file: start fresh instead of failing hard
	}
	r.Restore(snap.Messages, snap.Usage, snap.Model)
	return len(snap.Messages) > 0, nil
}

// ListSessions returns saved sessions, newest first.
func ListSessions(cfg config.Config) ([]SavedSession, error) {
	dir, err := sessionsDir()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	type item struct {
		s    SavedSession
		when time.Time
	}
	var items []item
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var snap SavedSession
		if json.Unmarshal(raw, &snap) != nil {
			continue
		}
		when, _ := time.Parse(time.RFC3339, snap.SavedAt)
		items = append(items, item{snap, when})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].when.After(items[j].when) })
	out := make([]SavedSession, 0, len(items))
	for _, it := range items {
		out = append(out, it.s)
	}
	return out, nil
}
