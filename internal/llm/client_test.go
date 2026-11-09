package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestChatStreamRequestsUsageAndParses(t *testing.T) {
	var streamOptions map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		so, ok := body["stream_options"].(map[string]any)
		if !ok {
			t.Errorf("stream_options missing in request")
		} else {
			streamOptions = so
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintln(w, `data: {"choices":[{"delta":{"content":"he"}}]}`)
		fmt.Fprintln(w, `data: {"choices":[{"delta":{"content":"llo"}}]}`)
		fmt.Fprintln(w, `data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}`)
		fmt.Fprintln(w, `data: [DONE]`)
	}))
	defer srv.Close()

	c := New(srv.URL, "k", "m")
	msg, usage, err := c.ChatStream(context.Background(), []Message{{Role: RoleUser, Content: "hi"}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if msg.Content != "hello" {
		t.Fatalf("content=%q", msg.Content)
	}
	if usage.Prompt != 12 || usage.Completion != 3 || usage.Total != 15 {
		t.Fatalf("usage=%+v", usage)
	}
	if streamOptions["include_usage"] != true {
		t.Fatalf("include_usage=%v", streamOptions["include_usage"])
	}
}

func TestChatStreamWithoutUsageKeepsZero(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, `data: {"choices":[{"delta":{"content":"x"}}]}`)
		fmt.Fprintln(w, `data: [DONE]`)
	}))
	defer srv.Close()

	c := New(srv.URL, "k", "m")
	_, usage, err := c.ChatStream(context.Background(), []Message{{Role: RoleUser, Content: "hi"}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !usage.IsZero() {
		t.Fatalf("usage should stay zero, got %+v", usage)
	}
}

func TestChatParsesUsage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"choices":[{"message":{"role":"assistant","content":"ok"}}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}`)
	}))
	defer srv.Close()

	c := New(srv.URL, "k", "m")
	_, usage, err := c.Chat(context.Background(), []Message{{Role: RoleUser, Content: "hi"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if usage.Total != 7 || usage.Prompt != 5 || usage.Completion != 2 {
		t.Fatalf("usage=%+v", usage)
	}
}

func TestUsageAdd(t *testing.T) {
	a := Usage{Prompt: 1, Completion: 2, Total: 3}
	b := a.Add(a)
	if b.Prompt != 2 || b.Completion != 4 || b.Total != 6 {
		t.Fatalf("add=%+v", b)
	}
}
