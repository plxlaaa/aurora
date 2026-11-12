package tui

import (
        "encoding/json"
        "strconv"
        "strings"
        "time"
)

var toolDisplay = map[string]string{
        "read_file":  "Read",
        "write_file": "Write",
        "edit_file":  "Edit",
        "list_dir":   "List",
        "glob":       "Glob",
        "grep":       "Grep",
        "run_shell":  "Bash",
}

var toolPrimaryArg = map[string]string{
        "read_file":  "path",
        "write_file": "path",
        "edit_file":  "path",
        "list_dir":   "path",
        "glob":       "pattern",
        "grep":       "pattern",
        "run_shell":  "command",
}

const cardMaxLines = 40

// cardElapsed formats a tool duration for the result line (" · 1.2s").
// Sub-100ms calls stay quiet to reduce noise, mirroring Claude Code.
func cardElapsed(d time.Duration) string {
        if d <= 0 {
                return ""
        }
        if d < 100*time.Millisecond {
                return ""
        }
        return " · " + d.Round(100*time.Millisecond).String()
}

// cardStats summarizes write/edit results as +N -N line counts
// parsed from the unified diff attached to the result.
func cardStats(diff string) string {
        if diff == "" {
                return ""
        }
        add, del := 0, 0
        for _, l := range strings.Split(diff, "\n") {
                switch {
                case strings.HasPrefix(l, "+") && !strings.HasPrefix(l, "+++"):
                        add++
                case strings.HasPrefix(l, "-") && !strings.HasPrefix(l, "---"):
                        del++
                }
        }
        if add == 0 && del == 0 {
                return ""
        }
        var b strings.Builder
        b.WriteString(" · ")
        if add > 0 {
                b.WriteString("+" + strconv.Itoa(add))
        }
        if del > 0 {
                if add > 0 {
                        b.WriteString(" ")
                }
                b.WriteString("-" + strconv.Itoa(del))
        }
        return b.String()
}

// toolCard renders one tool call line. The dot color reflects state:
// cyan while running, green on success, red on failure.
func toolCard(name, argsJSON, dotColor string) string {
        display := toolDisplay[name]
        if display == "" {
                display = name
        }
        dot := dotColor + "⏺" + cReset
        arg := primaryArg(name, argsJSON)
        if arg == "" {
                return dot + " " + cBold + display + cReset
        }
        return truncatePlain(dot+" "+cBold+display+cReset+"("+arg+")")
}

func truncatePlain(s string) string {
        max := termWidth() - 2
        r := []rune(s)
        if len(r) <= max || max < 4 {
                return s
        }
        return string(r[:max-1]) + "…"
}

func primaryArg(name, argsJSON string) string {
        var m map[string]any
        if err := json.Unmarshal([]byte(argsJSON), &m); err != nil {
                return trim(argsJSON, 64)
        }
        if key, has := toolPrimaryArg[name]; has {
                if v, ok := m[key].(string); ok && strings.TrimSpace(v) != "" {
                        return trim(v, 72)
                }
        }
        return ""
}

func toolResultLine(evOK bool, content string) string {
        // two-space indent forms the Claude-Code tree: ⏺ call on the
        // parent line, ⎿ result nested as its child
        mark := "  " + cRed + "⎿" + cReset
        if evOK {
                mark = "  " + cGreen + "⎿" + cReset
        }
        if evOK {
                return mark + " " + cDim + resultSummary(content) + cReset
        }
        return mark + " " + cRed + trim(content, 160) + cReset
}

func resultSummary(content string) string {
        c := strings.TrimSpace(content)
        if c == "" {
                return "no output"
        }
        lines := strings.Split(c, "\n")
        if len(lines) > 1 {
                return strconv.Itoa(len(lines)) + " lines"
        }
        return trim(lines[0], 96)
}

func renderDiff(diff string) string {
        lines := strings.Split(diff, "\n")
        var b strings.Builder
        shown := 0
        for _, l := range lines {
                if shown >= cardMaxLines {
                        b.WriteString(cDim + "  ... +" + strconv.Itoa(len(lines)-shown) + " more" + cReset + "\n")
                        break
                }
                switch {
                case strings.HasPrefix(l, "+"):
                        b.WriteString(cGreen + "+" + l[1:] + cReset + "\n")
                case strings.HasPrefix(l, "-"):
                        b.WriteString(cRed + "-" + l[1:] + cReset + "\n")
                case strings.HasPrefix(l, "..."):
                        b.WriteString(cDim + "  " + l + cReset + "\n")
                default:
                        b.WriteString(cDim + " " + l + cReset + "\n")
                }
                shown++
        }
        return strings.TrimSuffix(b.String(), "\n")
}
