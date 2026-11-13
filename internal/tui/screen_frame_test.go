package tui

import (
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/agent"
	"github.com/plxlaaa/aurora/internal/llm"
)

// Frame invariants: the fullscreen layout must never overflow the
// terminal (extra lines shake the frame every render) and the composer
// border must span the full width edge to edge.

func frameProbe(t *testing.T, m *screenModel, h int) {
	t.Helper()
	out := renderFrame(m)
	lines := strings.Split(out, "\n")
	if len(lines) != h {
		t.Errorf("frame lines=%d want=%d (overflow=%d)", len(lines), h, len(lines)-h)
	}
	for i, l := range lines {
		if w := visWidth(stripANSI(l)); w > m.width {
			t.Errorf("line %d width %d > %d: %q", i, w, m.width, stripANSI(l))
		}
	}
	if h > 4 {
		border := stripANSI(lines[len(lines)-2])
		if !strings.HasPrefix(border, "╰") || visWidth(border) != m.width {
			t.Errorf("composer bottom border width=%d want=%d: %q", visWidth(border), m.width, border)
		}
	}
}

func stripANSI(s string) string {
	var b strings.Builder
	inEsc := false
	for _, r := range s {
		if r == 0x1b {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
				inEsc = false
			}
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

func TestFrameSizes(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {120, 40}, {60, 20}, {40, 10}} {
		r := &agent.Runner{Client: &llm.Client{Model: "gpt-5.6-luna"}}
		m := newScreenModel(r, "C:\\Users\\plxl", "0.5.1")
		m.width, m.height = size[0], size[1]
		m.msgs = []viewMsg{
			{kind: msgUser, text: "你是谁？告诉我详细信息"},
			{kind: msgAI, text: "我是 Aurora，一个高性能终端编程助手，可以帮你阅读和修改代码。"},
			{kind: msgTool, name: "read_file", ok: true, done: true, text: "main.go → 45 lines"},
		}
		frameProbe(t, m, size[1])
	}
}

func TestFrameBusyKeepsHeight(t *testing.T) {
	r := &agent.Runner{Client: &llm.Client{Model: "m"}}
	m := newScreenModel(r, "/w", "v")
	m.width, m.height = 80, 24
	m.busy = true
	frameProbe(t, m, 24)
}

func TestLongCJKInputNoWrap(t *testing.T) {
	r := &agent.Runner{Client: &llm.Client{Model: "m"}}
	m := newScreenModel(r, "/w", "v")
	m.width, m.height = 80, 24
	m.input = []rune(strings.Repeat("输入内容", 30)) // 240 display cols > 80
	out := renderFrame(m)
	if lines := strings.Split(out, "\n"); len(lines) != 24 {
		t.Fatalf("long input must not grow frame: %d", len(lines))
	}
}

func TestLongModelNameNoMojibake(t *testing.T) {
	got := shortModel("deepseek-推理模型-特别长的名字-v2")
	if strings.ContainsRune(got, 0xFFFD) {
		t.Fatalf("rune cut produced replacement char: %q", got)
	}
}

func TestToolResultMatchesInterleaved(t *testing.T) {
	r := &agent.Runner{Client: &llm.Client{Model: "m"}}
	m := newScreenModel(r, "/w", "v")
	m = m.handleAgentEvent(agent.Event{Kind: "tool", Name: "read_file", Text: `{"path":"a"}`}).(*screenModel)
	m = m.handleAgentEvent(agent.Event{Kind: "tool", Name: "grep", Text: `{"pattern":"x"}`}).(*screenModel)
	m = m.handleAgentEvent(agent.Event{Kind: "tool_result", Name: "read_file", Text: "45 lines", OK: true}).(*screenModel)
	first := m.msgs[0]
	if !first.done || !first.ok {
		t.Fatalf("interleaved first card not resolved: %+v", first)
	}
	if m.msgs[1].done {
		t.Fatalf("grep card must stay pending: %+v", m.msgs[1])
	}
}
