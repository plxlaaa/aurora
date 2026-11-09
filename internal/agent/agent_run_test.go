package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/config"
)

const toolCallBody = `{"choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"t1","type":"function","function":{"name":"write_file","arguments":"{\"path\":\"out.txt\",\"content\":\"hello\\nworld\\n\"}"}}]}}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}`

const finalBody = `{"choices":[{"delta":{"content":"all done"}}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}`

func TestRunEventsUsageAndDiff(t *testing.T) {
	step := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		step++
		if step == 1 {
			fmt.Fprintf(w, "data: %s\n\n", toolCallBody)
		} else {
			fmt.Fprintf(w, "data: %s\n\n", finalBody)
		}
		fmt.Fprintln(w, "data: [DONE]")
	}))
	defer srv.Close()

	t.Setenv("AURORA_API_KEY", "test-key")
	cfg := config.Config{}
	cfg.LLM.BaseURL = srv.URL
	cfg.LLM.Model = "test-model"
	cfg.Agent.MaxSteps = 4

	r, err := New(cfg, t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	var kinds []string
	var diff, resText string
	var resOK bool
	r.On = func(ev Event) {
		kinds = append(kinds, ev.Kind)
		if ev.Kind == "tool_result" {
			diff = ev.Diff
			resText = ev.Text
			resOK = ev.OK
		}
	}
	final, turnUsage, err := r.Run(context.Background(), "write a file")
	if err != nil {
		t.Fatal(err)
	}
	if final != "all done" {
		t.Fatalf("final=%q", final)
	}
	if turnUsage.Total != 21 || turnUsage.Prompt != 15 || turnUsage.Completion != 6 {
		t.Fatalf("turn usage=%+v", turnUsage)
	}
	if r.SessionUsage().Total != 21 || r.LastUsage().Total != 7 {
		t.Fatalf("session=%+v last=%+v", r.SessionUsage(), r.LastUsage())
	}
	if diff == "" || !strings.Contains(diff, "+hello") || !strings.Contains(diff, "+world") {
		t.Fatalf("diff=%q ok=%v text=%q kinds=%v", diff, resOK, resText, kinds)
	}
	for _, want := range []string{"step", "tool", "tool_result", "done", "turn_end"} {
		found := false
		for _, k := range kinds {
			if k == want {
				found = true
			}
		}
		if !found {
			t.Fatalf("missing event %q in %v", want, kinds)
		}
	}
	if _, err := os.Stat(filepath.Join(r.Tools.Root, "out.txt")); err != nil {
		t.Fatalf("tool did not write file: %v", err)
	}
}

func TestToolPayloadExcludesDiff(t *testing.T) {
	step := 0
	var secondBody string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		step++
		raw, _ := io.ReadAll(r.Body)
		if step == 2 {
			secondBody = string(raw)
		}
		if step == 1 {
			fmt.Fprintf(w, "data: %s\n\n", toolCallBody)
		} else {
			fmt.Fprintf(w, "data: %s\n\n", finalBody)
		}
		fmt.Fprintln(w, "data: [DONE]")
	}))
	defer srv.Close()

	t.Setenv("AURORA_API_KEY", "test-key")
	cfg := config.Config{}
	cfg.LLM.BaseURL = srv.URL
	cfg.LLM.Model = "test-model"
	cfg.Agent.MaxSteps = 4
	r, err := New(cfg, t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := r.Run(context.Background(), "write a file"); err != nil {
		t.Fatal(err)
	}
	var parsed struct {
		Messages []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal([]byte(secondBody), &parsed); err != nil {
		t.Fatal(err)
	}
	for _, m := range parsed.Messages {
		if m.Role == "tool" && (strings.Contains(m.Content, `"Diff"`) || strings.Contains(m.Content, `"+`)) {
			t.Fatalf("diff leaked into tool payload: %s", m.Content)
		}
	}
}
