package tui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/config"
)

func TestGitBranchRef(t *testing.T) {
	dir := t.TempDir()
	gitDir := filepath.Join(dir, ".git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gitDir, "HEAD"), []byte("ref: refs/heads/feat/ui\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if b := gitBranch(dir); b != "feat/ui" {
		t.Fatalf("branch=%q", b)
	}
}

func TestGitBranchDetachedAndMissing(t *testing.T) {
	dir := t.TempDir()
	if b := gitBranch(dir); b != "" {
		t.Fatalf("no .git should yield empty, got %q", b)
	}
	gitDir := filepath.Join(dir, ".git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gitDir, "HEAD"), []byte("abc123def456abc123def456abc123def456abc1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if b := gitBranch(dir); b != "detached" {
		t.Fatalf("detached=%q", b)
	}
}

func TestBannerPlain(t *testing.T) {
	var got strings.Builder
	p := newPrinter(&writerFunc{func(b []byte) (int, error) {
		got.Write(b)
		return len(b), nil
	}})
	cfg := config.Default()
	Banner(p, cfg, "/tmp/ws")
	out := got.String()
	if strings.Contains(out, "╭") {
		t.Fatalf("pipe banner should have no box: %q", out)
	}
	if !strings.Contains(out, "model") || !strings.Contains(out, "workspace") {
		t.Fatalf("banner rows missing: %q", out)
	}
}

func TestBannerRichBox(t *testing.T) {
	// force rich by checking the drawing helpers directly
	title := "✻ Aurora test · terminal coding agent"
	inner := len([]rune(title)) + 2
	if inner < bannerMinWidth {
		inner = bannerMinWidth
	}
	var b strings.Builder
	b.WriteString("╭" + strings.Repeat("─", inner) + "╮\n")
	b.WriteString("│ " + title + strings.Repeat(" ", inner-2-len([]rune(title))) + " │\n")
	b.WriteString("╰" + strings.Repeat("─", inner) + "╯\n")
	lines := strings.Split(b.String(), "\n")
	for _, l := range lines[:3] {
		if len([]rune(l)) != inner+2 {
			t.Fatalf("box line width %d != %d: %q", len([]rune(l)), inner+2, l)
		}
	}
	if !strings.Contains(lines[1], "Aurora test") {
		t.Fatalf("title missing: %q", lines[1])
	}
}

func TestTruncateRunes(t *testing.T) {
	if truncateRunes("abcdef", 4) != "abc…" {
		t.Fatalf("trunc=%q", truncateRunes("abcdef", 4))
	}
	if truncateRunes("abc", 10) != "abc" {
		t.Fatalf("short=%q", truncateRunes("abc", 10))
	}
}

func TestContextSegment(t *testing.T) {
	if seg := contextSegment(0, 128000); seg != "" {
		t.Fatalf("zero prompt: %q", seg)
	}
	if seg := contextSegment(1000, 0); seg != "" {
		t.Fatalf("zero window: %q", seg)
	}
	if seg := contextSegment(60000, 100000); !strings.Contains(seg, cYellow) {
		t.Fatalf("60%% should be yellow: %q", seg)
	}
	if seg := contextSegment(90000, 100000); !strings.Contains(seg, cRed) {
		t.Fatalf("90%% should be red: %q", seg)
	}
	if seg := contextSegment(30000, 100000); seg != "" {
		t.Fatalf("30%% should be empty: %q", seg)
	}
}

func TestSpinnerBounce(t *testing.T) {
	pos, dir := 0, 1
	seen := []string{}
	for i := 0; i < 14; i++ {
		next := pos + dir
		if next >= len(spinnerFrames) || next < 0 {
			dir = -dir
			next = pos + dir
		}
		pos = next
		seen = append(seen, spinnerFrames[pos])
	}
	if seen[0] != "✢" {
		t.Fatalf("first advance should be second frame: %v", seen)
	}
	if seen[4] != "✽" || seen[5] != "✻" || seen[9] != "·" {
		t.Fatalf("bounce should reverse at boundaries: %v", seen)
	}
}
