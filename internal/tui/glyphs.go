package tui

// Glyph set: Unicode symbols (⏺ ⎿ ✻ ◐ ☑) render beautifully on modern
// terminals (Windows Terminal, VS Code, macOS/Linux) but Windows classic
// consoles (conhost with legacy codepages like cp936/cp437) show them as
// '?' garbage. Codex/Claude Code solve this with an ASCII fallback set.
//
// SetUnicode(true) is called when the terminal looks UTF-8 capable.

var glyphUnicode = true

// SetUnicode switches the glyph set. Called once at REPL start.
func SetUnicode(on bool) { glyphUnicode = on }

func pick(uni, ascii string) string {
	if glyphUnicode {
		return uni
	}
	return ascii
}

// tool call / result tree
func glyphToolDot() string    { return pick("⏺", "*") }
func glyphToolBranch() string { return pick("⎿", "`->") }
func glyphTurnBranch() string { return pick("⎿", "\\") }

// spinner frames and the thinking notice
func glyphSpinnerFrames() []string { return []string{"|", "/", "-", "\\"} }
func glyphThink() string          { return pick("✻", "*") }

// plan checklist states
func glyphPlanPending() string   { return pick("☐", "[ ]") }
func glyphPlanActive() string    { return pick("◐", "[~]") }
func glyphPlanDone() string     { return pick("☑", "[x]") }

// markdown decorations
func glyphBullet() string  { return pick("•", "-") }
func glyphQuote() string   { return pick("▏", ">") }
