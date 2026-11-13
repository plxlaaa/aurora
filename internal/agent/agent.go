package agent

import (
        "context"
        "encoding/json"
        "fmt"
        "os"
        "path/filepath"
        "strings"

        "github.com/plxlaaa/aurora/internal/config"
        "github.com/plxlaaa/aurora/internal/llm"
        "github.com/plxlaaa/aurora/internal/tools"
)

const systemPrompt = `You are Aurora, a high-performance terminal coding agent.
Work inside the given workspace. Prefer small, precise edits.
Use tools to inspect files before changing them.
Keep answers concise. When a task is done, summarize what changed.
Never print secrets. Never run destructive shell commands unless the user asked.
On Windows prefer PowerShell syntax.`

type Event struct {
        Kind  string
        Name  string
        Text  string
        OK    bool
        Diff  string
        Usage llm.Usage
}

type Runner struct {
        Client *llm.Client
        Tools  *tools.Registry
        Max    int
        On     func(Event)
        msgs   []llm.Message
        usage  llm.Usage
        last   llm.Usage
}

func New(cfg config.Config, workspace string, confirm func(string) bool) (*Runner, error) {
        key := config.APIKey(cfg)
        if key == "" {
                return nil, fmt.Errorf("set %s (OpenAI-compatible API key)", cfg.LLM.APIKeyEnv)
        }
        if workspace == "" {
                workspace, _ = os.Getwd()
        }
        abs, err := filepath.Abs(workspace)
        if err != nil {
                return nil, err
        }
        r := &Runner{
                Client: llm.New(cfg.LLM.BaseURL, key, cfg.LLM.Model),
                Tools: &tools.Registry{
                        Root:         abs,
                        ConfirmShell: cfg.Agent.ConfirmShell,
                        Confirm:      confirm,
                },
                Max: cfg.Agent.MaxSteps,
        }
        r.Reset()
        return r, nil
}

func (r *Runner) Reset() {
        r.msgs = []llm.Message{{
                Role:    llm.RoleSystem,
                Content: systemPrompt + "\nWorkspace: " + r.Tools.Root + projectMemory(r.Tools.Root),
        }}
}

func (r *Runner) SetModel(model string) {
        if r.Client != nil && strings.TrimSpace(model) != "" {
                r.Client.Model = strings.TrimSpace(model)
        }
}

// SetConfirm swaps the shell confirmation callback at runtime
// (fullscreen /yolo toggle).
func (r *Runner) SetConfirm(fn func(string) bool) {
        if r.Tools != nil {
                r.Tools.Confirm = fn
        }
}

func (r *Runner) Compact() {
        if len(r.msgs) <= 1 {
                return
        }
        sys := r.msgs[0]
        keep := r.msgs[1:]
        if len(keep) > 8 {
                keep = keep[len(keep)-8:]
        }
        r.msgs = append([]llm.Message{sys}, keep...)
}

func (r *Runner) Turns() int {
        n := 0
        for _, m := range r.msgs {
                if m.Role == llm.RoleUser {
                        n++
                }
        }
        return n
}

func (r *Runner) Messages() int {
        return len(r.msgs)
}

func (r *Runner) SessionUsage() llm.Usage {
        return r.usage
}

func (r *Runner) LastUsage() llm.Usage {
        return r.last
}

// Model returns the current model id (for session snapshots).
func (r *Runner) Model() string {
        if r.Client == nil {
                return ""
        }
        return r.Client.Model
}

// Snapshot copies conversation messages (without mutating the runner).
func (r *Runner) Snapshot() []llm.Message {
        out := make([]llm.Message, len(r.msgs))
        copy(out, r.msgs)
        return out
}

// Restore replaces conversation state from a saved session.
func (r *Runner) Restore(msgs []llm.Message, usage llm.Usage, model string) {
        if len(msgs) == 0 {
                return
        }
        r.msgs = make([]llm.Message, len(msgs))
        copy(r.msgs, msgs)
        r.usage = usage
        r.last = llm.Usage{}
        r.SetModel(model)
}

func projectMemory(root string) string {
        for _, name := range []string{"AURORA.md", "AGENTS.md"} {
                raw, err := os.ReadFile(filepath.Join(root, name))
                if err != nil {
                        continue
                }
                text := strings.TrimSpace(string(raw))
                if text == "" {
                        continue
                }
                if len(text) > 8000 {
                        text = text[:8000] + "\n..."
                }
                return "\nProject file " + name + ":\n" + text
        }
        return ""
}

func (r *Runner) Run(ctx context.Context, user string) (string, llm.Usage, error) {
        if strings.TrimSpace(user) == "" {
                return "", llm.Usage{}, fmt.Errorf("empty prompt")
        }
        if len(r.msgs) == 0 {
                r.Reset()
        }
        r.msgs = append(r.msgs, llm.Message{Role: llm.RoleUser, Content: user})
        specs := tools.Specs()
        final := ""
        turnUsage := llm.Usage{}
        steps := r.Max
        if steps <= 0 {
                steps = 16
        }
        defer func() {
                if r.On != nil {
                        r.On(Event{Kind: "turn_end", Usage: turnUsage})
                }
        }()
        for i := 0; i < steps; i++ {
                if r.On != nil {
                        r.On(Event{Kind: "step", Text: fmt.Sprintf("%d/%d", i+1, steps)})
                }
                msg, stepUsage, err := r.Client.ChatStream(ctx, r.msgs, specs, func(delta string) {
                        if r.On != nil {
                                r.On(Event{Kind: "delta", Text: delta})
                        }
                })
                if err != nil && msg.Content == "" && len(msg.ToolCalls) == 0 {
                        msg, stepUsage, err = r.Client.Chat(ctx, r.msgs, specs)
                        if err != nil {
                                return final, turnUsage, err
                        }
                        if r.On != nil && msg.Content != "" {
                                r.On(Event{Kind: "delta", Text: msg.Content})
                        }
                } else if err != nil {
                        return final, turnUsage, err
                }
                r.usage = r.usage.Add(stepUsage)
                r.last = stepUsage
                turnUsage = turnUsage.Add(stepUsage)
                if r.On != nil && !stepUsage.IsZero() {
                        r.On(Event{Kind: "usage", Usage: stepUsage})
                }
                if msg.Role == "" {
                        msg.Role = llm.RoleAssistant
                }
                r.msgs = append(r.msgs, msg)
                if len(msg.ToolCalls) == 0 {
                        final = strings.TrimSpace(msg.Content)
                        if r.On != nil {
                                r.On(Event{Kind: "done", Text: final})
                        }
                        if len(r.msgs) > 48 {
                                r.Compact()
                        }
                        return final, turnUsage, nil
                }
                for _, call := range msg.ToolCalls {
                        if r.On != nil {
                                r.On(Event{Kind: "tool", Name: call.Function.Name, Text: call.Function.Arguments})
                        }
                        res := r.Tools.Call(ctx, call.Function.Name, call.Function.Arguments)
                        payload, _ := json.Marshal(res)
                        r.msgs = append(r.msgs, llm.Message{
                                Role:       llm.RoleTool,
                                ToolCallID: call.ID,
                                Name:       call.Function.Name,
                                Content:    string(payload),
                        })
                        if r.On != nil {
                                r.On(Event{Kind: "tool_result", Name: call.Function.Name, Text: res.Content, OK: res.OK, Diff: res.Diff})
                        }
                }
        }
        return final, turnUsage, fmt.Errorf("reached max agent steps (%d)", steps)
}
