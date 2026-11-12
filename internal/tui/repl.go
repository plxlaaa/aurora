package tui

import (
        "bufio"
        "context"
        "encoding/json"
        "fmt"
        "io"
        "os"
        "os/signal"
        "path/filepath"
        "strconv"
        "strings"
        "time"

        "github.com/plxlaaa/aurora/internal/agent"
        "github.com/plxlaaa/aurora/internal/config"
        "github.com/plxlaaa/aurora/internal/tools"
        "github.com/plxlaaa/aurora/internal/updater"
        "github.com/plxlaaa/aurora/internal/version"
)

const (
        cReset  = "\033[0m"
        cDim    = "\033[2m"
        cCyan   = "\033[36m"
        cGreen  = "\033[32m"
        cYellow = "\033[33m"
        cRed    = "\033[31m"
        cBold   = "\033[1m"
)

var replCommands = []struct{ name, desc string }{
        {"/help", "show this help"},
        {"/init", "scan the workspace and write AURORA.md project notes"},
        {"/diff", "show git diff of the workspace"},
        {"/status", "session, model, workspace, tokens"},
        {"/usage", "token usage for session and last step"},
        {"/context", "context window breakdown and compact hint"},
        {"/history", "user prompts sent this session"},
        {"/sessions", "list saved sessions (workspace scoped)"},
        {"/resume", "restore the last saved session for this workspace"},
        {"/todos", "show the current task plan"},
        {"/model <name>", "switch model; aliases: sonnet, opus, gpt5, mini"},
        {"/cd <path>", "change workspace and reset session"},
        {"/reset", "clear conversation memory (alias /clear)"},
        {"/compact", "keep recent turns only"},
        {"/yolo", "skip shell confirmation this session"},
        {"/update", "check and apply local update"},
        {"/config", "print config path"},
        {"/doctor", "diagnose install and update channels"},
        {"/quit", "exit (alias /exit)"},
}

func (s *Session) Help() {
        s.out.Printf("\n%scommands%s\n", cBold, cReset)
        for _, c := range replCommands {
                s.out.Printf("  %s%-16s%s %s%s%s\n", cBold, c.name, cReset, cDim, c.desc, cReset)
        }
        s.out.Printf("\n%splain text is sent to the agent. conversation memory is kept until /reset.%s\n\n", cDim, cReset)
}

func Confirm(in *bufio.Reader, command string) bool {
        fmt.Printf("%srun shell?%s %s\n[y/N] ", cYellow, cReset, command)
        line, _ := in.ReadString('\n')
        line = strings.TrimSpace(strings.ToLower(line))
        return line == "y" || line == "yes"
}

type Session struct {
        Cfg       config.Config
        Workspace string
        Runner    *agent.Runner
        Reader    *bufio.Reader
        Yolo      bool
        rich      bool
        out       *printer
        spin      *spinner
        buf       *lineBuffer
        pendName  string
        pendArgs  string
        pendAt    time.Time
        turnStart time.Time
        think     ThinkFilter
        history   []string
        resumed   bool
}

func newSession(cfg config.Config, workspace string, reader *bufio.Reader) *Session {
        s := &Session{
                Cfg:       cfg,
                Workspace: workspace,
                Reader:    reader,
                rich:      IsTTY(),
        }
        s.out = newPrinter(os.Stdout)
        s.spin = newSpinner(s.out)
        s.buf = &lineBuffer{rich: s.rich}
        return s
}

func (s *Session) rebuild() error {
        ws := s.Workspace
        r, err := agent.New(s.Cfg, ws, func(cmd string) bool {
                if s.Yolo {
                        return true
                }
                return s.confirmShell(cmd)
        })
        if err != nil {
                return err
        }
        s.Runner = r
        s.attach()
        return nil
}

func (s *Session) confirmShell(cmd string) bool {
        s.pendName = ""
        s.out.Printf("%srun shell?%s %s\n[y/N/!] ", cYellow, cReset, cmd)
        line, _ := s.Reader.ReadString('\n')
        line = strings.TrimSpace(strings.ToLower(line))
        switch line {
        case "y", "yes":
                return true
        case "!":
                s.Yolo = true // ! approves once and yolo mode for the rest of the session
                s.out.Printf("%syolo mode enabled%s\n", cYellow, cReset)
                return true
        default:
                return false
        }
}

func (s *Session) attach() {
        s.Runner.On = func(ev agent.Event) {
                switch ev.Kind {
                case "step":
                        s.spin.Start()
                case "delta":
                        s.spin.Stop()
                        s.pendName = ""
                        s.buf.Feed(s.think.Feed(ev.Text), s.out)
                case "tool":
                        s.spin.Stop()
                        s.think.Reset()
                        if s.rich {
                                s.pendName = ev.Name
                                s.pendArgs = ev.Text
                                s.pendAt = time.Now()
                                s.out.Printf("\n%s\n", toolCard(ev.Name, ev.Text, cCyan))
                        } else {
                                s.out.Printf("\n%s▸ %s%s %s\n", cYellow, ev.Name, cReset, cDim+trim(ev.Text, 120)+cReset)
                        }
                case "tool_result":
                        if ev.Name == "update_plan" {
                                s.showPlan(ev.Text)
                                s.pendName = ""
                                return
                        }
                        if !s.rich {
                                color := cGreen
                                if !ev.OK {
                                        color = cRed
                                }
                                s.out.Printf("%s%s%s\n", color, trim(ev.Text, 400), cReset)
                                return
                        }
                        dot := cGreen
                        if !ev.OK {
                                dot = cRed
                        }
                        if s.pendName != "" {
                                s.out.Printf("\033[1A\033[2K%s\n", toolCard(s.pendName, s.pendArgs, dot))
                                s.pendName = ""
                        }
                        elapsed := time.Since(s.pendAt)
                        s.out.Printf("%s%s%s%s\n", toolResultLine(ev.OK, ev.Text),
                                cDim, cardElapsed(elapsed)+cardStats(ev.Diff), cReset)
                        if ev.Diff != "" {
                                s.out.Printf("%s\n", renderDiff(ev.Diff))
                        }
                        s.out.Print("\n") // blank line closes the tool block
                case "done":
                        s.buf.Flush(s.out)
                        if ev.Text != "" && !strings.HasSuffix(ev.Text, "\n") {
                                s.out.Print("\n")
                        }
                case "turn_end":
                        s.spin.Stop()
                        s.pendName = ""
                }
        }
}

// showPlan prints the Claude-Code-style checklist for update_plan results.
func (s *Session) showPlan(resultJSON string) {
        var parsed struct {
                OK      bool                 `json:"ok"`
                Content string               `json:"content"`
                Steps   []tools.PlanStep     `json:"steps"`
        }
        if err := json.Unmarshal([]byte(resultJSON), &parsed); err != nil {
                return
        }
        steps := parsed.Steps
        if len(steps) == 0 {
                steps = tools.CurrentPlan()
        }
        if glyphUnicode {
                s.out.Printf("%s\n", tools.RenderPlan(steps))
        } else {
                s.out.Printf("%s\n", tools.RenderPlanASCII(steps))
        }
}

func (s *Session) runTurn(ctx context.Context, line string) {
        s.buf.Reset()
        s.pendName = ""
        s.think = ThinkFilter{}
        s.history = append(s.history, line)
        s.turnStart = time.Now()
        turnCtx, stop := signal.NotifyContext(ctx, os.Interrupt)
        defer stop()
        _, _, err := s.Runner.Run(turnCtx, line)
        s.spin.Stop()
        if err != nil {
                if turnCtx.Err() != nil {
                        s.out.Printf("%sturn cancelled%s\n", cYellow, cReset)
                } else {
                        s.out.Printf("%serror:%s %v\n", cRed, cReset, err)
                }
                s.out.Print("\n")
                return
        }
        s.printTurnSummary()
        s.out.Print("\n")
        if err := agent.SaveSession(s.Cfg, s.Workspace, s.Runner); err != nil && verboseSave {
                s.out.Printf("%ssession autosave failed: %v%s\n", cDim, err, cReset)
        }
}

// printTurnSummary closes a finished turn with the Claude-Code-style tree
// footer. One dim line under the answer: ⎿ tokens · elapsed · context headroom.
// Model/workspace live in the banner and /status — no need to repeat here.
func (s *Session) printTurnSummary() {
        u := s.Runner.LastUsage()
        elapsed := time.Since(s.turnStart).Seconds()
        parts := []string{
                fmt.Sprintf("↑%s ↓%s", fmtTokens(u.Prompt), fmtTokens(u.Completion)),
                fmt.Sprintf("%.1fs", elapsed),
        }
        if cw := s.Cfg.LLM.ContextWindow; cw > 0 && u.Prompt > 0 {
                pct := int(float64(u.Prompt) * 100 / float64(cw))
                parts = append(parts, fmt.Sprintf("ctx %d%%", pct))
        }
        s.out.Printf("\n%s%s%s %s%s%s\n", cDim, glyphTurnBranch(), cReset, cDim, strings.Join(parts, " · "), cReset)
}

func contextSegment(prompt, window int64) string {
        if window <= 0 || prompt <= 0 {
                return ""
        }
        pct := int(float64(prompt) * 100 / float64(window))
        if pct >= 80 {
                return cRed + fmt.Sprintf("context %d%% used, /compact", pct) + cReset
        }
        if pct >= 60 {
                return cYellow + fmt.Sprintf("context %d%% used, /compact", pct) + cReset
        }
        return ""
}

func fmtTokens(n int64) string {
        if n <= 0 {
                return "n/a"
        }
        if n < 1000 {
                return strconv.FormatInt(n, 10)
        }
        return fmt.Sprintf("%.1fk", float64(n)/1000)
}

// ResumeChoice controls session restore at REPL startup.
type ResumeChoice int

const (
        ResumeAsk    ResumeChoice = iota // interactive prompt when a saved session exists
        ResumeYes                        // always restore (--resume / --continue)
        ResumeNo                         // never restore
)

func RunREPL(ctx context.Context, cfg config.Config, workspace string, in io.Reader, updateFn func() error, resume ResumeChoice) error {
        EnableColor()
        if workspace == "" {
                workspace, _ = os.Getwd()
        }
        abs, err := filepath.Abs(workspace)
        if err == nil {
                workspace = abs
        }
        reader := bufio.NewReader(in)
        s := newSession(cfg, workspace, reader)
        if err := s.rebuild(); err != nil {
                return err
        }
        Banner(s.out, cfg, workspace)
        hintUpdate(cfg)
        s.maybeResume(resume)

        for {
                s.out.Printf("%s>%s ", cCyan, cReset)
                line, err := reader.ReadString('\n')
                if err == io.EOF {
                        fmt.Println()
                        return nil
                }
                if err != nil {
                        return err
                }
                line = strings.TrimSpace(line)
                if line == "" {
                        continue
                }
                if strings.HasPrefix(line, "!") && len(line) > 1 {
                        s.runLocalShell(ctx, line[1:])
                        continue
                }
                switch {
                case line == "/help":
                        s.Help()
                case line == "/quit" || line == "/exit":
                        return nil
                case line == "/init":
                        s.cmdInit()
                case line == "/diff":
                        s.cmdDiff(ctx)
                case line == "/context":
                        s.cmdContext()
                case line == "/history":
                        s.cmdHistory()
                case line == "/sessions":
                        s.cmdSessions()
                case line == "/resume":
                        s.cmdResume()
                case line == "/todos":
                        s.showPlan("{}")
                case line == "/doctor":
                        s.cmdDoctor(ctx)
                case line == "/config":
                        p, _ := config.Path()
                        fmt.Println(p)
                case line == "/status":
                        u := s.Runner.SessionUsage()
                        last := s.Runner.LastUsage()
                        ctxLine := "n/a"
                        if cw := s.Cfg.LLM.ContextWindow; cw > 0 && last.Prompt > 0 {
                                ctxLine = fmt.Sprintf("%d%% of %s", int(float64(last.Prompt)*100/float64(cw)), fmtTokens(cw))
                        }
                        s.out.Printf("version %s\nmodel %s\nworkspace %s\ngit %s\nyolo %v\nturns %d  messages %d\ntokens session %s\ncontext %s\n",
                                version.Short(), s.Cfg.LLM.Model, s.Workspace, gitBranch(s.Workspace), s.Yolo,
                                s.Runner.Turns(), s.Runner.Messages(), fmtTokens(u.Total), ctxLine)
                case line == "/usage":
                        u := s.Runner.SessionUsage()
                        last := s.Runner.LastUsage()
                        s.out.Printf("%ssession%s  prompt %s  completion %s  total %s\n",
                                cDim, cReset, fmtTokens(u.Prompt), fmtTokens(u.Completion), fmtTokens(u.Total))
                        s.out.Printf("%slast%s     prompt %s  completion %s  total %s\n",
                                cDim, cReset, fmtTokens(last.Prompt), fmtTokens(last.Completion), fmtTokens(last.Total))
                case line == "/reset" || line == "/clear":
                        s.Runner.Reset()
                        fmt.Println("session cleared")
                case line == "/compact":
                        s.Runner.Compact()
                        fmt.Println("session compacted")
                case line == "/yolo":
                        s.Yolo = !s.Yolo
                        _ = s.rebuild()
                        fmt.Println("yolo =", s.Yolo)
                case line == "/update":
                        if updateFn == nil {
                                fmt.Println("updater unavailable")
                                continue
                        }
                        if err := updateFn(); err != nil {
                                fmt.Printf("%supdate failed:%s %v\n", cRed, cReset, err)
                        }
                case line == "/model":
                        fmt.Println("model =", s.Runner.Model())
                case strings.HasPrefix(line, "/model "):
                        name := resolveModelAlias(strings.TrimSpace(strings.TrimPrefix(line, "/model ")))
                        s.Cfg.LLM.Model = name
                        s.Runner.SetModel(name)
                        fmt.Println("model =", s.Cfg.LLM.Model)
                case strings.HasPrefix(line, "/cd "):
                        next := strings.TrimSpace(strings.TrimPrefix(line, "/cd "))
                        if next == "" {
                                fmt.Println(s.Workspace)
                                continue
                        }
                        abs, err := filepath.Abs(next)
                        if err != nil {
                                fmt.Println(err)
                                continue
                        }
                        st, err := os.Stat(abs)
                        if err != nil || !st.IsDir() {
                                fmt.Println("not a directory:", abs)
                                continue
                        }
                        s.Workspace = abs
                        if err := s.rebuild(); err != nil {
                                fmt.Println(err)
                                continue
                        }
                        fmt.Println("workspace =", s.Workspace)
                default:
                        s.runTurn(ctx, line)
                }
        }
}

func hintUpdate(cfg config.Config) {
        if !cfg.Update.Enabled {
                return
        }
        ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
        defer cancel()
        res, err := updater.Check(ctx, cfg, version.Short(), nil)
        if err != nil {
                return
        }
        if updater.Newer(res.To, version.Short()) {
                fmt.Printf("%supdate available %s -> %s  run /update%s\n\n", cDim, version.Short(), res.To, cReset)
        }
}

func trim(s string, n int) string {
        s = strings.TrimSpace(strings.ReplaceAll(s, "\n", " "))
        if len(s) <= n {
                return s
        }
        return s[:n] + "..."
}

func IsTTY() bool {
        st, err := os.Stdout.Stat()
        if err != nil {
                return false
        }
        return st.Mode()&os.ModeCharDevice != 0
}

func IsInteractiveTTY() bool {
        st, err := os.Stdin.Stat()
        if err != nil {
                return false
        }
        return st.Mode()&os.ModeCharDevice != 0
}
