package tools

import "testing"

func TestUpdatePlanRoundtrip(t *testing.T) {
	ResetPlan()
	res := updatePlan(`{"steps":[
		{"text":"read code","status":"done"},
		{"text":"apply fix","status":"in_progress"},
		{"text":"run tests","status":"pending"},
		{"text":"weird status","status":"whatever"}
	]}`)
	if !res.OK {
		t.Fatalf("update_plan failed: %s", res.Content)
	}
	steps := CurrentPlan()
	if len(steps) != 4 {
		t.Fatalf("want 4 steps, got %d", len(steps))
	}
	if steps[0].Status != PlanDone || steps[1].Status != PlanInProgress || steps[2].Status != PlanPending || steps[3].Status != PlanPending {
		t.Fatalf("normalize failed: %+v", steps)
	}
	ResetPlan()
	if len(CurrentPlan()) != 0 {
		t.Fatal("reset failed")
	}
}

func TestUpdatePlanEmpty(t *testing.T) {
	if res := updatePlan(`{"steps":[]}`); res.OK {
		t.Fatal("empty plan must fail")
	}
}

func TestUpdatePlanTruncatesLongText(t *testing.T) {
	long := make([]byte, 300)
	for i := range long {
		long[i] = 'x'
	}
	res := updatePlan(`{"steps":[{"text":"` + string(long) + `","status":"pending"}]}`)
	if !res.OK {
		t.Fatalf("plan failed: %s", res.Content)
	}
	if len(CurrentPlan()[0].Text) > 120+3 {
		t.Fatalf("text not truncated: %d", len(CurrentPlan()[0].Text))
	}
}

func TestRenderPlan(t *testing.T) {
	out := RenderPlan([]PlanStep{
		{Text: "one", Status: PlanDone},
		{Text: "two", Status: PlanInProgress},
		{Text: "three", Status: PlanPending},
	})
	for _, want := range []string{"☑ one", "◐ two", "☐ three", "plan 1/3"} {
		if !contains(out, want) {
			t.Fatalf("missing %q in %q", want, out)
		}
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 || indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func TestPlanSpecInSpecs(t *testing.T) {
	found := false
	for _, sp := range Specs() {
		if sp.Function.Name == "update_plan" {
			found = true
		}
	}
	if !found {
		t.Fatal("update_plan missing from Specs()")
	}
}
