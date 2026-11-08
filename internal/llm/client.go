package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Role string

const (
	RoleSystem    Role = "system"
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
	RoleTool      Role = "tool"
)

type Message struct {
	Role       Role       `json:"role"`
	Content    string     `json:"content,omitempty"`
	Name       string     `json:"name,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
}

type ToolCall struct {
	ID       string       `json:"id"`
	Type     string       `json:"type"`
	Function FunctionCall `json:"function"`
}

type FunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type ToolSpec struct {
	Type     string       `json:"type"`
	Function ToolFunction `json:"function"`
}

type ToolFunction struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Parameters  json.RawMessage `json:"parameters"`
}

type Client struct {
	BaseURL    string
	APIKey     string
	Model      string
	HTTPClient *http.Client
}

func New(baseURL, apiKey, model string) *Client {
	baseURL = strings.TrimRight(baseURL, "/")
	return &Client{
		BaseURL: baseURL,
		APIKey:  apiKey,
		Model:   model,
		HTTPClient: &http.Client{
			Timeout: 0,
			Transport: &http.Transport{
				Proxy:                 http.ProxyFromEnvironment,
				MaxIdleConns:          16,
				IdleConnTimeout:       90 * time.Second,
				TLSHandshakeTimeout:   10 * time.Second,
				ResponseHeaderTimeout: 60 * time.Second,
			},
		},
	}
}

type chatRequest struct {
	Model       string     `json:"model"`
	Messages    []Message  `json:"messages"`
	Tools       []ToolSpec `json:"tools,omitempty"`
	Stream      bool       `json:"stream,omitempty"`
	MaxTokens   int        `json:"max_tokens,omitempty"`
	Temperature float64    `json:"temperature,omitempty"`
}

type chatResponse struct {
	Choices []struct {
		Message      Message `json:"message"`
		FinishReason string  `json:"finish_reason"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error,omitempty"`
}

func (c *Client) Chat(ctx context.Context, messages []Message, tools []ToolSpec) (Message, error) {
	if c.APIKey == "" {
		return Message{}, fmt.Errorf("missing API key")
	}
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	body, err := json.Marshal(chatRequest{
		Model:       c.Model,
		Messages:    messages,
		Tools:       tools,
		Temperature: 0.2,
	})
	if err != nil {
		return Message{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return Message{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return Message{}, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return Message{}, err
	}
	var parsed chatResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return Message{}, fmt.Errorf("decode llm response: %w (%s)", err, truncate(string(raw), 240))
	}
	if parsed.Error != nil && parsed.Error.Message != "" {
		return Message{}, fmt.Errorf("llm: %s", parsed.Error.Message)
	}
	if resp.StatusCode >= 300 {
		return Message{}, fmt.Errorf("llm http %d: %s", resp.StatusCode, truncate(string(raw), 240))
	}
	if len(parsed.Choices) == 0 {
		return Message{}, fmt.Errorf("llm returned empty choices")
	}
	return parsed.Choices[0].Message, nil
}

func (c *Client) ChatStream(ctx context.Context, messages []Message, tools []ToolSpec, onDelta func(string)) (Message, error) {
	if c.APIKey == "" {
		return Message{}, fmt.Errorf("missing API key")
	}
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	body, err := json.Marshal(chatRequest{
		Model:       c.Model,
		Messages:    messages,
		Tools:       tools,
		Stream:      true,
		Temperature: 0.2,
	})
	if err != nil {
		return Message{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return Message{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	req.Header.Set("Accept", "text/event-stream")
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return Message{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(resp.Body)
		return Message{}, fmt.Errorf("llm http %d: %s", resp.StatusCode, truncate(string(raw), 240))
	}

	out := Message{Role: RoleAssistant}
	calls := map[int]*ToolCall{}
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if payload == "[DONE]" {
			break
		}
		var chunk streamChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			continue
		}
		if len(chunk.Choices) == 0 {
			continue
		}
		delta := chunk.Choices[0].Delta
		if delta.Content != "" {
			out.Content += delta.Content
			if onDelta != nil {
				onDelta(delta.Content)
			}
		}
		for _, tc := range delta.ToolCalls {
			cur, ok := calls[tc.Index]
			if !ok {
				cur = &ToolCall{Type: "function", ID: fmt.Sprintf("call_%d", tc.Index)}
				calls[tc.Index] = cur
			}
			if tc.ID != "" {
				cur.ID = tc.ID
			}
			if tc.Function.Name != "" {
				cur.Function.Name = tc.Function.Name
			}
			if tc.Function.Arguments != "" {
				cur.Function.Arguments += tc.Function.Arguments
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return out, err
	}
	if len(calls) > 0 {
		out.ToolCalls = make([]ToolCall, 0, len(calls))
		for i := 0; i < len(calls); i++ {
			if c, ok := calls[i]; ok {
				out.ToolCalls = append(out.ToolCalls, *c)
			}
		}
	}
	return out, nil
}

type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Index    int    `json:"index"`
				ID       string `json:"id"`
				Type     string `json:"type"`
				Function struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"delta"`
	} `json:"choices"`
}

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
