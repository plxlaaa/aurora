package agent

import (
        "strings"
        "testing"

        "github.com/plxlaaa/aurora/internal/config"
        "github.com/plxlaaa/aurora/internal/llm"
)

func TestSaveLoadSessionRoundtrip(t *testing.T) {
        cfg := config.Default()
        workspace := t.TempDir()
        r := &Runner{
                Client: &llm.Client{Model: "test-model"},
                usage:  llm.Usage{Prompt: 10, Completion: 5, Total: 15},
                msgs: []llm.Message{
                        {Role: llm.RoleSystem, Content: "sys"},
                        {Role: llm.RoleUser, Content: "hello"},
                        {Role: llm.RoleAssistant, Content: "hi"},
                },
        }

        if err := SaveSession(cfg, workspace, r); err != nil {
                t.Fatalf("save: %v", err)
        }

        r2 := &Runner{Client: &llm.Client{Model: "other"}}
        ok, err := LoadSession(cfg, workspace, r2)
        if err != nil || !ok {
                t.Fatalf("load: ok=%v err=%v", ok, err)
        }
        if r2.Model() != "test-model" {
                t.Fatalf("model not restored: %q", r2.Model())
        }
        if len(r2.Snapshot()) != len(r.Snapshot()) {
                t.Fatalf("messages not restored: %d vs %d", len(r2.Snapshot()), len(r.Snapshot()))
        }
        if r2.SessionUsage().Total != 15 {
                t.Fatalf("usage not restored: %+v", r2.SessionUsage())
        }
}

func TestListSessionsNewestFirst(t *testing.T) {
        cfg := config.Default()
        w1, w2 := t.TempDir(), t.TempDir()
        for i, w := range []string{w1, w2} {
                r := &Runner{Client: &llm.Client{Model: strings.Repeat("m", i+1)},
                        msgs: []llm.Message{{Role: llm.RoleUser, Content: "x"}}}
                if err := SaveSession(cfg, w, r); err != nil {
                        t.Fatal(err)
                }
        }
        list, err := ListSessions(cfg)
        if err != nil || len(list) < 2 {
                t.Fatalf("list: n=%d err=%v", len(list), err)
        }
}

func TestLoadSessionEmptyNoop(t *testing.T) {
        cfg := config.Default()
        r := &Runner{Client: &llm.Client{Model: "keep"},
                msgs: []llm.Message{{Role: llm.RoleSystem, Content: "s"}}}
        ok, err := LoadSession(cfg, t.TempDir(), r)
        if err != nil {
                t.Fatal(err)
        }
        if ok {
                t.Fatal("empty dir must not resume")
        }
        if r.Model() != "keep" {
                t.Fatal("runner mutated on empty load")
        }
}
