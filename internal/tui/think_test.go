package tui

import (
	"strings"
	"testing"
	"time"
)

func TestThinkHidden(t *testing.T) {
	var f ThinkFilter
	out := f.Feed("before<think>secret reasoning</think>after")
	if strings.Contains(out, "secret") {
		t.Fatalf("thinking leaked: %q", out)
	}
	if !strings.Contains(out, "before") || !strings.Contains(out, "after") {
		t.Fatalf("content lost: %q", out)
	}
}

func TestThinkAcrossDeltas(t *testing.T) {
	var f ThinkFilter
	var got strings.Builder
	for _, d := range []string{"hi <th", "ink>deep", " stuff</thi", "nk>done"} {
		got.WriteString(f.Feed(d))
	}
	s := got.String()
	if strings.Contains(s, "deep") || strings.Contains(s, " stuff") {
		t.Fatalf("thinking leaked across deltas: %q", s)
	}
	if !strings.Contains(s, "hi") || !strings.Contains(s, "done") {
		t.Fatalf("content lost: %q", s)
	}
}

func TestThinkingTagVariant(t *testing.T) {
	var f ThinkFilter
	out := f.Feed("<thinking>x</thinking>y")
	if strings.Contains(out, "x") || !strings.Contains(out, "y") {
		t.Fatalf("variant tag failed: %q", out)
	}
}

func TestThinkNoticeElapsed(t *testing.T) {
	var f ThinkFilter
	f.Feed("<think>")
	f.started = f.started.Add(-3 * time.Second)
	out := f.Feed("</think>")
	if !strings.Contains(out, "✻") || !strings.Contains(out, "3s") {
		t.Fatalf("elapsed notice missing: %q", out)
	}
}

func TestThinkReset(t *testing.T) {
	var f ThinkFilter
	f.Feed("<think>")
	f.Reset()
	if out := f.Feed("plain"); out != "plain" {
		t.Fatalf("reset failed: %q", out)
	}
}
