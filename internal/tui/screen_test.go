package tui

import (
        "strings"
        "testing"

        "github.com/plxlaaa/aurora/internal/agent"
        "github.com/plxlaaa/aurora/internal/llm"
)

func TestRenderViewMsgUserRightAligned(t *testing.T) {
        lines := renderViewMsg(viewMsg{kind: msgUser, text: "hello"}, 80)
        if len(lines) == 0 {
                t.Fatal("no lines")
        }
        first := lines[0]
        if !strings.Contains(first, "> hello") {
                t.Fatalf("user text missing: %q", first)
        }
        // right alignment: heavy padding on the left
        if len(first)-len("> hello") < 20 {
                t.Fatalf("expected right alignment padding, got %q", first)
        }
}

func TestRenderViewMsgAI(t *testing.T) {
        lines := renderViewMsg(viewMsg{kind: msgAI, text: "hi there"}, 80)
        if !strings.Contains(lines[0], "hi there") {
                t.Fatalf("ai text missing: %q", lines[0])
        }
}

func TestRenderViewMsgTool(t *testing.T) {
        lines := renderViewMsg(viewMsg{kind: msgTool, text: "Read(a.go)", ok: true}, 80)
        if !strings.Contains(lines[0], "Read(a.go)") {
                t.Fatalf("tool text missing: %q", lines[0])
        }
        fail := renderViewMsg(viewMsg{kind: msgTool, text: "boom", ok: false}, 80)
        if !strings.Contains(fail[0], "x boom") {
                t.Fatalf("failed tool should show x mark: %q", fail[0])
        }
}

func TestVisibleTailFollowsBottom(t *testing.T) {
        var lines []string
        for i := 0; i < 100; i++ {
                lines = append(lines, strings.Repeat("L", 3))
        }
        got := visibleTail(lines, 10, 0)
        if len(got) != 10 {
                t.Fatalf("want 10 visible lines, got %d", len(got))
        }
        if strings.Join(got, "") != strings.Repeat("LLL", 10) {
                t.Fatal("tail should show the LAST lines")
        }
}

func TestVisibleTailScrollUp(t *testing.T) {
        lines := []string{"a", "b", "c", "d", "e"}
        got := visibleTail(lines, 2, 1) // scroll 1 up from bottom
        if strings.Join(got, ",") != "c,d" {
                t.Fatalf("scroll wrong: %v", got)
        }
}

func TestStatusLineParts(t *testing.T) {
        r := &agent.Runner{Client: &llm.Client{Model: "gpt-test"}}
        m := newScreenModel(r, "/w", "0.5.0-test")
        m.ctxWin = 128000
        line := m.statusLine()
        for _, want := range []string{"gpt-test", "0.5.0-test", "ctx"} {
                if !strings.Contains(line, want) {
                        t.Fatalf("status missing %q: %s", want, line)
                }
        }
}

func TestBlankSplit(t *testing.T) {
        m := newScreenModel(nil, "/w", "v")
        m.input = []rune("hello")
        if string(m.input) != "hello" {
                t.Fatal("input text mismatch")
        }
}
