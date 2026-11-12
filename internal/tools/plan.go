package tools

import (
	"encoding/json"
	"strings"
)

// PlanStep mirrors the plan-tracking flow popularized by Claude Code's
// TodoWrite and Codex's update_plan: the model keeps a visible checklist
// so long tasks have an inspectable progress bar in the terminal.
type PlanStep struct {
	Text   string `json:"text"`
	Status string `json:"status"` // pending | in_progress | done
}

const (
	PlanPending    = "pending"
	PlanInProgress = "in_progress"
	PlanDone       = "done"
)

var (
	planMu    = &syncMutex{}
	planSteps []PlanStep
)

type syncMutex struct{ m chan struct{} }

func (m *syncMutex) lock()   { m.m <- struct{}{} }
func (m *syncMutex) unlock() { <-m.m }

func init() { planMu.m = make(chan struct{}, 1) }

func normalizeStatus(s string) string {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case PlanInProgress, "active", "current", "doing":
		return PlanInProgress
	case PlanDone, "completed", "complete", "finished", "ok":
		return PlanDone
	default:
		return PlanPending
	}
}

// updatePlan replaces the whole checklist and renders it back.
func updatePlan(argsJSON string) Result {
	var args struct {
		Steps []PlanStep `json:"steps"`
	}
	if strings.TrimSpace(argsJSON) == "" {
		return fail("update_plan: steps is required")
	}
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return fail("invalid plan arguments: " + err.Error())
	}
	steps := make([]PlanStep, 0, len(args.Steps))
	for _, s := range args.Steps {
		t := strings.TrimSpace(s.Text)
		if t == "" {
			continue
		}
		if len(t) > 120 {
			t = t[:120] + "..."
		}
		steps = append(steps, PlanStep{Text: t, Status: normalizeStatus(s.Status)})
	}
	if len(steps) == 0 {
		return fail("update_plan: at least one step required")
	}
	planMu.lock()
	planSteps = steps
	planMu.unlock()
	return ok(RenderPlan(steps))
}

// CurrentPlan returns a copy of the active plan (for tests and /todos).
func CurrentPlan() []PlanStep {
	planMu.lock()
	defer planMu.unlock()
	out := make([]PlanStep, len(planSteps))
	copy(out, planSteps)
	return out
}

// ResetPlan clears the checklist (new session / /reset).
func ResetPlan() {
	planMu.lock()
	planSteps = nil
	planMu.unlock()
}

// RenderPlan draws the Claude-Code-style checklist: ☐ pending, ◐ active, ☑ done.
// ANSI state colors are applied when enabled; plain otherwise.
func RenderPlan(steps []PlanStep) string {
	var b strings.Builder
	done := 0
	for _, s := range steps {
		switch s.Status {
		case PlanDone:
			done++
			b.WriteString("☑ " + s.Text + "\n")
		case PlanInProgress:
			b.WriteString("◐ " + s.Text + "\n")
		default:
			b.WriteString("☐ " + s.Text + "\n")
		}
	}
	if len(steps) > 0 {
		b.WriteString("plan " + itoa(done) + "/" + itoa(len(steps)))
	}
	return b.String()
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
