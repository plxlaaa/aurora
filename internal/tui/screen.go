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
                if m.scroll < len(m.msgs) {
                        m.scroll++
                }
                return m, nil
        case tea.KeyPgDown:
                if m.scroll > 0 {
                        m.scroll--
                }
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
                if n := len(m.msgs); n > 0 && m.msgs[n-1].kind == msgTool && m.msgs[n-1].name == ev.Name {
                        m.msgs[n-1] = viewMsg{
                                kind: msgTool, name: ev.Name, ok: ev.OK,
                                text: m.msgs[n-1].text + " → " + trim(ev.Text, 60),
                        }
                } else {
                        m.msgs = append(m.msgs, viewMsg{kind: msgTool, name: ev.Name, ok: ev.OK, text: trim(ev.Text, 60)})
                }
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

        var b strings.Builder
        b.WriteString(strings.Join(lines, "\n"))
        b.WriteString("\n")

        if m.busy {
                d := time.Since(m.turnAt).Round(time.Second)
                fmt.Fprintf(&b, " %s Thinking… %s (Ctrl+C interrupt)\n", glyphThink(), d)
        }

        line := string(m.input)
        if line == "" && !m.busy {
                line = "type a task… (/help · Ctrl+C exit · PgUp scroll)"
        }
        b.WriteString(screenStyles.inputBorder.Width(w - 4).Render("> " + line))
        b.WriteString("\n")

        b.WriteString(screenStyles.statusStyle.Render(m.statusLine()))
        return b.String()
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
        if len(name) > 28 {
                return name[:25] + "…"
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
