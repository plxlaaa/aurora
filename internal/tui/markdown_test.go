package tui

import (
	"strings"
	"testing"
)

func TestRenderHeading(t *testing.T) {
	out := renderMarkdownLine("# Title", &mdState{})
	if !strings.Contains(out, cBold+cCyan+"Title") {
		t.Fatalf("h1=%q", out)
	}
	out = renderMarkdownLine("## Sub", &mdState{})
	if !strings.Contains(out, cBold+"Sub") || strings.Contains(out, cCyan) {
		t.Fatalf("h2=%q", out)
	}
}

func TestRenderBullet(t *testing.T) {
	out := renderMarkdownLine("- item one", &mdState{})
	if !strings.Contains(out, "•") || strings.Contains(out, "- item") {
		t.Fatalf("bullet=%q", out)
	}
}

func TestRenderInlineCodeAndBold(t *testing.T) {
	out := renderMarkdownLine("use `fmt.Println` and **care**", &mdState{})
	if !strings.Contains(out, cCyan+"fmt.Println"+cReset) {
		t.Fatalf("code=%q", out)
	}
	if !strings.Contains(out, cBold+"care"+cReset) {
		t.Fatalf("bold=%q", out)
	}
}

func TestRenderFenceState(t *testing.T) {
	st := &mdState{}
	if renderMarkdownLine("```go", st) == "" {
		t.Fatal("open fence should render rule")
	}
	if !st.inFence {
		t.Fatal("fence should open")
	}
	out := renderMarkdownLine("x := 1", st)
	if !strings.Contains(out, cDim) || !strings.HasPrefix(out, "  ") {
		t.Fatalf("fence line=%q", out)
	}
	renderMarkdownLine("```", st)
	if st.inFence {
		t.Fatal("fence should close")
	}
}

func TestPlainLinesUnchanged(t *testing.T) {
	in := "plain text line"
	if out := renderMarkdownLine(in, &mdState{}); out != in {
		t.Fatalf("plain=%q", out)
	}
}

func TestLineBufferFeeds(t *testing.T) {
	var got strings.Builder
	p := newPrinter(&writerFunc{func(b []byte) (int, error) {
		got.Write(b)
		return len(b), nil
	}})
	lb := &lineBuffer{rich: false}
	lb.Feed("hel", p)
	lb.Feed("lo\nwor", p)
	lb.Feed("ld", p)
	lb.Flush(p)
	if got.String() != "hello\nworld" {
		t.Fatalf("out=%q", got.String())
	}
}

func TestToolCard(t *testing.T) {
	card := toolCard("read_file", `{"path":"src/a.go","offset":3}`)
	if !strings.Contains(card, "Read") || !strings.Contains(card, "(src/a.go)") {
		t.Fatalf("card=%q", card)
	}
	if strings.Contains(card, "offset") {
		t.Fatalf("card should show primary arg only: %q", card)
	}
	card = toolCard("run_shell", `{"command":"go test ./..."}`)
	if !strings.Contains(card, "Bash") || !strings.Contains(card, "(go test ./...)") {
		t.Fatalf("card=%q", card)
	}
}

func TestToolResultLine(t *testing.T) {
	out := toolResultLine(true, "l1\nl2\nl3")
	if !strings.Contains(out, "3 lines") {
		t.Fatalf("ok=%q", out)
	}
	out = toolResultLine(false, "boom: bad thing")
	if !strings.Contains(out, "boom") {
		t.Fatalf("fail=%q", out)
	}
}

func TestRenderDiffColors(t *testing.T) {
	out := renderDiff("-old\nnew\n+added\n... +5 more")
	if !strings.Contains(out, cRed+"-old") || !strings.Contains(out, cGreen+"+added") {
		t.Fatalf("diff=%q", out)
	}
}

type writerFunc struct {
	fn func([]byte) (int, error)
}

func (w *writerFunc) Write(b []byte) (int, error) { return w.fn(b) }
