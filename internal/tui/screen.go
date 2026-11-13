package tui

import (
        "context"
        "fmt"
        "strings"
        "time"

        tea "github.com/charmbracelet/bubbletea"
        "github.com/charmbracelet/lipgloss"

        "github.com/plxlaaa/aurora/internal/agent"
)

// Full-screen TUI (v0.5): fixed bottom composer + one-line status bar,
// chat-style transcript (user right, assistant left), thinking indicator
// pinned right above the composer. Built on bubbletea/lipgloss — the
// layout Codex and Claude Code use in modern terminals.

type viewMsgKind string

const (
        msgUser   viewMsgKind = "user"
        msgAI     viewMsgKind = "ai"
        msgTool   viewMsgKind = "tool"
        msgSystem viewMsgKind = "system"
)

type viewMsg struct {
        kind viewMsgKind
        text string
        name string
        ok   bool
        done bool // tool card has received its result
}

// tea messages
type evMsg agent.Event
type doneMsg struct{ err error }
type tickMsg struct{}

type screenModel struct {
        runner    *agent.Runner
        workspace string
        version   string

        msgs     []viewMsg
        input    []rune
        busy     bool
        turnAt   time.Time
        dur      time.Duration
        scroll   int // manual scroll offset from bottom (0 = follow)
        lastLn   int // transcript line count of the last render (scroll clamp)
        width    int
        height   int
        errText  string
        think    ThinkFilter
        prog     *tea.Program
        ctxWin   int64
        cancelFn context.CancelFunc
}

var screenStyles = struct {
        userStyle   lipgloss.Style
        aiStyle     lipgloss.Style
        toolStyle   lipgloss.Style
        toolFail    lipgloss.Style
        systemStyle lipgloss.Style
        inputBorder lipgloss.Style
        statusStyle lipgloss.Style
}{
        userStyle:   lipgloss.NewStyle().Foreground(lipgloss.Color("6")).Align(lipgloss.Right).PaddingRight(2),
        aiStyle:     lipgloss.NewStyle().PaddingLeft(2),
        toolStyle:   lipgloss.NewStyle().Foreground(lipgloss.Color("8")).PaddingLeft(2),
        toolFail:    lipgloss.NewStyle().Foreground(lipgloss.Color("1")).PaddingLeft(2),
        systemStyle: lipgloss.NewStyle().Foreground(lipgloss.Color("8")).PaddingLeft(2),
        inputBorder: lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("6")).Padding(0, 1),
        statusStyle: lipgloss.NewStyle().Foreground(lipgloss.Color("8")).PaddingLeft(2),
}

func newScreenModel(runner *agent.Runner, workspace, version string) *screenModel {
        return &screenModel{
                runner:    runner,
                workspace: workspace,
                version:   version,
                input:     []rune{},
        }
}

func (m *screenModel) Init() tea.Cmd {
        return tea.EnterAltScreen
}

func (m *screenModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
        switch v := msg.(type) {
        case tea.WindowSizeMsg:
                m.width, m.height = v.Width, v.Height
                return m, nil

        case tea.KeyMsg:
                return m.handleKey(v)

        case evMsg:
                return m.handleAgentEvent(agent.Event(v)), nil

        case doneMsg:
                m.busy = false
                m.dur = time.Since(m.turnAt)
                if v.err != nil {
                        m.errText = v.err.Error()
                }
                return m, nil

        case tickMsg:
                if m.busy {
                        return m, tea.Tick(time.Second, func(time.Time) tea.Msg { return tickMsg{} })
                }
                return m, nil
        }
        return m, nil
}

func (m *screenModel) handleKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
        switch k.Type {
        case tea.KeyCtrlC:
                if m.busy && m.cancelFn != nil {
                        m.cancelFn() // interrupt the running turn; a second Ctrl+C exits
                        m.busy = false
                        m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "turn cancelled"})
                        return m, nil
                }
                return m, tea.Quit
        case tea.KeyEnter:
                if m.busy {
                        return m, nil
                }
                return m, m.send()
        case tea.KeyPgUp:
                // clamp against the last rendered line count; visibleTail
                // clamps again so overscroll can never break the frame
                if m.scroll < m.lastLn {
                        m.scroll++
                }
                return m, nil
        case tea.KeyPgDown:
                if m.scroll > 0 {
                        m.scroll--
                }
                return m, nil
        case tea.KeyHome:
                m.scroll = m.lastLn
                return m, nil
        case tea.KeyEnd:
                m.scroll = 0
                return m, nil
        case tea.KeyBackspace:
                if len(m.input) > 0 {
                        m.input = m.input[:len(m.input)-1]
                }
                return m, nil
        case tea.KeyRunes, tea.KeySpace:
                m.input = append(m.input, k.Runes...)
                return m, nil
        }
        return m, nil
}

func (m *screenModel) send() tea.Cmd {
        text := strings.TrimSpace(string(m.input))
        if text == "" {
                return nil
        }
        m.input = m.input[:0]
        m.errText = ""
        m.dur = 0 // reset last-turn duration for the status bar
        m.think = ThinkFilter{}

        // slash commands run locally inside the composer
        if strings.HasPrefix(text, "/") {
                m.handleCommand(text)
                return nil
        }

        m.msgs = append(m.msgs, viewMsg{kind: msgUser, text: text})
        m.busy = true
        m.turnAt = time.Now()

        runCtx, cancel := context.WithCancel(context.Background())
        m.cancelFn = cancel

        prog, runner := m.prog, m.runner
        startTick := tea.Tick(time.Second, func(time.Time) tea.Msg { return tickMsg{} })
        return tea.Batch(startTick, func() tea.Msg {
                old := runner.On
                runner.On = func(ev agent.Event) {
                        if prog != nil {
                                prog.Send(evMsg(ev))
                        }
                }
                _, _, err := runner.Run(runCtx, text)
                runner.On = old
                return doneMsg{err: err}
        })
}

// handleCommand runs REPL slash commands; output becomes transcript lines.
func (m *screenModel) handleCommand(text string) {
        switch {
        case text == "/quit" || text == "/exit":
                if m.prog != nil {
                        m.prog.Quit()
                }
        case text == "/clear" || text == "/reset":
                m.msgs = m.msgs[:0]
                m.runner.Reset()
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "session cleared"})
        case text == "/compact":
                m.runner.Compact()
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "session compacted"})
        case text == "/yolo":
                m.runner.SetConfirm(func(string) bool { return true })
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "yolo on — shell runs without confirmation"})
        case text == "/status":
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem,
                        text: fmt.Sprintf("model %s · workspace %s · turns %d · v%s",
                                m.runner.Model(), m.workspace, m.runner.Turns(), m.version)})
        case strings.HasPrefix(text, "/model "):
                name := resolveModelAlias(strings.TrimSpace(strings.TrimPrefix(text, "/model ")))
                m.runner.SetModel(name)
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "model = " + name})
        case text == "/model":
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "model = " + m.runner.Model()})
        case text == "/help":
                var b strings.Builder
                b.WriteString("commands:\n")
                for _, c := range replCommands {
                        fmt.Fprintf(&b, "  %-18s %s\n", c.name, c.desc)
                }
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: b.String()})
        default:
                m.msgs = append(m.msgs, viewMsg{kind: msgSystem,
                        text: text + " — use line mode (`aurora --line`) for this command"})
        }
}

func (m *screenModel) handleAgentEvent(ev agent.Event) tea.Model {
        switch ev.Kind {
        case "delta":
                if out := m.think.Feed(ev.Text); out != "" {
                        if n := len(m.msgs); n > 0 && m.msgs[n-1].kind == msgAI {
                                m.msgs[n-1].text += out
                        } else {
                                m.msgs = append(m.msgs, viewMsg{kind: msgAI, text: out})
                        }
                }
        case "tool":
                m.msgs = append(m.msgs, viewMsg{kind: msgTool, name: ev.Name, text: trim(ev.Text, 80)})
        case "tool_result":
                if ev.Name == "update_plan" {
                        m.msgs = append(m.msgs, viewMsg{kind: msgSystem, text: "plan updated"})
                        return m
                }
                // find the most recent unfinished card with the same tool name —
                // interleaved tool calls (read→edit→read) must not leave the
                // first card stuck in its argument-only state
                for i := len(m.msgs) - 1; i >= 0; i-- {
                        vm := m.msgs[i]
                        if vm.kind == msgTool && vm.name == ev.Name && !vm.done {
                                vm.ok = ev.OK
                                vm.done = true
                                vm.text += " → " + trim(ev.Text, 60)
                                m.msgs[i] = vm
                                return m
                        }
                }
                m.msgs = append(m.msgs, viewMsg{kind: msgTool, name: ev.Name, ok: ev.OK, done: true, text: trim(ev.Text, 60)})
        }
        return m
}

func (m *screenModel) View() string {
        if m.width == 0 {
                return "loading…"
        }
        return renderFrame(m)
}

// renderFrame composes the fixed layout. Pure function of the model so
// the layout is unit-testable without a terminal.
func renderFrame(m *screenModel) string {
        w := m.width
        if w < 20 {
                w = 20
        }

        var lines []string
        for _, msg := range m.msgs {
                lines = append(lines, renderViewMsg(msg, w)...)
        }

        const inputH, statusH = 3, 1
        thinkH := 0
        if m.busy {
                thinkH = 1
        }
        transcriptH := m.height - inputH - statusH - thinkH
        if transcriptH < 1 {
                transcriptH = 1
        }

        if len(lines) > transcriptH {
                lines = visibleTail(lines, transcriptH, m.scroll)
        } else if len(lines) < transcriptH {
                pad := make([]string, transcriptH-len(lines))
                lines = append(pad, lines...)
        }
        m.lastLn = len(lines) // scroll clamp for PgUp/Home

        var b strings.Builder
        b.WriteString(strings.Join(lines, "\n"))
        b.WriteString("\n")

        if m.busy {
                d := time.Since(m.turnAt).Round(time.Second)
                fmt.Fprintf(&b, " %s Thinking… %s (Ctrl+C interrupt)\n", glyphThink(), d)
        }

        // composer: border draws +2 columns around its Width() content area,
        // so Width(w-2) fills the full terminal width edge to edge
        line := string(m.input)
        if line == "" && !m.busy {
                line = "type a task… (/help · Ctrl+C exit · PgUp scroll)"
        }
        b.WriteString(screenStyles.inputBorder.Width(w - 2).Render("> " + truncateVis(line, w-6)))
        b.WriteString("\n")

        // statusStyle has PaddingLeft(2) on top of the rendered content, so
        // the truncation budget is w-2 to keep the bar on one physical line
        b.WriteString(screenStyles.statusStyle.Render(truncateVis(m.statusLine(), w-2)))
        return b.String()
}

// truncateVis cuts s to at most max display columns, wide-rune aware
// (CJK counts 2). Keeps composer/status to a single physical line —
// overflow would push the frame taller and shake the layout.
func truncateVis(s string, max int) string {
        if max < 4 {
                return s
        }
        if visWidth(s) <= max {
                return s
        }
        var b strings.Builder
        w := 0
        for _, r := range s {
                rw := runeW(r)
                if w+rw > max-1 {
                        break
                }
                b.WriteRune(r)
                w += rw
        }
        return b.String() + "…"
}

func runeW(r rune) int {
        if r >= 0x1100 && ((r >= 0x2E80 && r <= 0xA4CF) || (r >= 0xAC00 && r <= 0xD7A3) ||
                (r >= 0xF900 && r <= 0xFAFF) || (r >= 0xFF00 && r <= 0xFF60) || (r >= 0xFFE0 && r <= 0xFFE6)) {
                return 2
        }
        return 1
}

func visWidth(s string) int {
        w := 0
        for _, r := range s {
                w += runeW(r)
        }
        return w
}

// visibleTail returns the last `h` lines of `lines`, scrolled up by
// `up` lines (0 = follow the bottom). Clamp keeps partial views valid.
func visibleTail(lines []string, h, up int) []string {
        if h < 1 {
                h = 1
        }
        if up < 0 {
                up = 0
        }
        if up > len(lines)-h {
                up = len(lines) - h
                if up < 0 {
                        up = 0
                }
        }
        end := len(lines) - up
        if end > len(lines) {
                end = len(lines)
        }
        start := end - h
        if start < 0 {
                start = 0
        }
        return lines[start:end]
}

// statusLine is the fixed bottom bar: model · workspace · context ·
// turns · duration · version. Extracted from View for testability.
func (m *screenModel) statusLine() string {
        ctxPct := 0
        if m.runner != nil {
                u := m.runner.LastUsage()
                if m.ctxWin > 0 && u.Prompt > 0 {
                        ctxPct = int(float64(u.Prompt) * 100 / float64(m.ctxWin))
                }
        }
        dur := ""
        if m.dur > 0 {
                dur = fmt.Sprintf(" · %.1fs", m.dur.Seconds())
        }
        turns := 0
        if m.runner != nil {
                turns = m.runner.Turns()
        }
        status := fmt.Sprintf("%s · %s · ctx %d%% · turns %d%s · v%s",
                shortModel(m.modelName()), shortDir(m.workspace), ctxPct, turns, dur, m.version)
        if m.errText != "" {
                status = "error: " + trim(m.errText, 120) + " · " + status
        }
        return status
}

func (m *screenModel) modelName() string {
        if m.runner == nil {
                return ""
        }
        return m.runner.Model()
}

func shortModel(name string) string {
        // rune-safe: byte slicing would cut CJK/model names mid-codepoint
        // and print mojibake in the status bar
        rs := []rune(name)
        if len(rs) > 28 {
                return string(rs[:25]) + "…"
        }
        return name
}

func shortDir(ws string) string {
        parts := strings.Split(strings.ReplaceAll(ws, "\\", "/"), "/")
        if len(parts) == 0 {
                return ws
        }
        return parts[len(parts)-1]
}

// renderViewMsg turns one logical message into printable lines.
func renderViewMsg(msg viewMsg, width int) []string {
        w := width - 8
        if w < 10 {
                w = 10
        }
        split := func(s string) []string { return strings.Split(s, "\n") }
        switch msg.kind {
        case msgUser:
                return split(screenStyles.userStyle.Width(w).Render("> " + msg.text))
        case msgAI:
                return split(screenStyles.aiStyle.Width(w).Render(msg.text))
        case msgTool:
                mark := glyphToolDot()
                style := screenStyles.toolStyle
                if !msg.ok {
                        mark = "x"
                        style = screenStyles.toolFail
                }
                return split(style.Width(w).Render(mark + " " + msg.text))
        default:
                return split(screenStyles.systemStyle.Width(w).Render(msg.text))
        }
}

// RunScreen starts the fullscreen TUI. Falls back to the line-mode REPL
// when stdin is not an interactive terminal.
func RunScreen(runner *agent.Runner, workspace, version string, ctxWindow int64) error {
        m := newScreenModel(runner, workspace, version)
        m.ctxWin = ctxWindow
        p := tea.NewProgram(m, tea.WithAltScreen())
        m.prog = p
        _, err := p.Run()
        return err
}
