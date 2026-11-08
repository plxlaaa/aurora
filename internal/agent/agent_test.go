package agent

import (
	"testing"

	"github.com/plxlaaa/aurora/internal/llm"
	"github.com/plxlaaa/aurora/internal/tools"
)

func TestResetAndCompact(t *testing.T) {
	r := &Runner{
		Tools: &tools.Registry{Root: t.TempDir()},
		Max:   8,
	}
	r.Reset()
	if len(r.msgs) != 1 || r.msgs[0].Role != llm.RoleSystem {
		t.Fatal("reset should keep system prompt")
	}
	for i := 0; i < 20; i++ {
		r.msgs = append(r.msgs, llm.Message{Role: llm.RoleUser, Content: "u"})
		r.msgs = append(r.msgs, llm.Message{Role: llm.RoleAssistant, Content: "a"})
	}
	if r.Turns() != 20 {
		t.Fatalf("turns=%d", r.Turns())
	}
	r.Compact()
	if len(r.msgs) > 9 {
		t.Fatalf("compacted len=%d", len(r.msgs))
	}
	if r.msgs[0].Role != llm.RoleSystem {
		t.Fatal("system prompt dropped")
	}
}
