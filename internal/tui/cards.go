package tui

import (
	"encoding/json"
	"strconv"
	"strings"
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
	mark := cRed + "⎿" + cReset
	if evOK {
		mark = cGreen + "⎿" + cReset
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
