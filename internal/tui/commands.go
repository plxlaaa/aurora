package tui

import (
        "bufio"
        "context"
        "fmt"
        "os"
        "os/exec"
        "path/filepath"
        "runtime"
        "strings"
        "time"

        "github.com/plxlaaa/aurora/internal/agent"
        "github.com/plxlaaa/aurora/internal/config"
        "github.com/plxlaaa/aurora/internal/updater"
        "github.com/plxlaaa/aurora/internal/version"
)

// verboseSave keeps autosave failures quiet unless debugging.
const verboseSave = false

// modelAliases expand short names to common relay model ids,
// inspired by Claude Code's /model and typical relay catalogs.
var modelAliases = map[string]string{
        "sonnet":  "claude-sonnet-4-5-20250929",
        "opus":    "claude-opus-4-1-20250805",
        "haiku":   "claude-3-5-haiku-20241022",
        "gpt5":    "gpt-5.2",
        "gpt5m":   "gpt-5.2-mini",
        "mini":    "gpt-4o-mini",
        "o3":      "o3",
        "deepseek": "deepseek-chat",
        "qwq":     "qwq-32b-preview",
}

func resolveModelAlias(name string) string {
        if full, ok := modelAliases[strings.ToLower(strings.TrimSpace(name))]; ok {
                return full
        }
        return strings.TrimSpace(name)
}

// maybeResume restores the saved session for this workspace.
func (s *Session) maybeResume(choice ResumeChoice) {
        if choice == ResumeNo {
                return
        }
        ok, err := agent.LoadSession(s.Cfg, s.Workspace, s.Runner)
        if err != nil || !ok {
                return
        }
        s.resumed = true
        if choice == ResumeAsk {
                if !IsInteractiveTTY() {
                        return // non-interactive stdin can't answer the prompt
                }
                s.out.Printf("%srestore last session for this workspace? [Y/n]%s ", cYellow, cReset)
                line, _ := s.Reader.ReadString('\n')
                line = strings.TrimSpace(strings.ToLower(line))
                if line == "n" || line == "no" {
                        s.Runner.Reset()
                        s.resumed = false
                }
        }
        if s.resumed {
                s.out.Printf("%sresumed %d messages (%s)%s\n", cDim, s.Runner.Messages(), s.Runner.Model(), cReset)
        }
}

func (s *Session) cmdResume() {
        ok, err := agent.LoadSession(s.Cfg, s.Workspace, s.Runner)
        if err != nil {
                fmt.Printf("%sresume failed:%s %v\n", cRed, cReset, err)
                return
        }
        if !ok {
                fmt.Println("no saved session for this workspace")
                return
        }
        s.resumed = true
        fmt.Printf("resumed %d messages\n", s.Runner.Messages())
}

func (s *Session) cmdSessions() {
        list, err := agent.ListSessions(s.Cfg)
        if err != nil || len(list) == 0 {
                fmt.Println("no saved sessions")
                return
        }
        for _, it := range list {
                when := ""
                if t, e := time.Parse(time.RFC3339, it.SavedAt); e == nil {
                        when = t.Local().Format("2006-01-02 15:04")
                }
                fmt.Printf("%s  %s%-20s%s  %s  %d msgs\n", when, cBold, filepath.Base(it.Workspace), cReset, it.Model, len(it.Messages))
        }
}

func (s *Session) cmdHistory() {
        if len(s.history) == 0 {
                fmt.Println("no prompts yet this session")
                return
        }
        for i, h := range s.history {
                fmt.Printf("%s%2d%s %s\n", cDim, i+1, cReset, trim(h, 160))
        }
}

func (s *Session) cmdContext() {
        last := s.Runner.LastUsage()
        cw := s.Cfg.LLM.ContextWindow
        if cw <= 0 {
                fmt.Println("context window: unknown (set llm.context_window in config)")
                return
        }
        prompt := last.Prompt
        if prompt <= 0 {
                prompt = int64(s.Runner.Messages()) * 400 // rough estimate before first turn
        }
        pct := int(float64(prompt) * 100 / float64(cw))
        color := cGreen
        if pct >= 80 {
                color = cRed
        } else if pct >= 60 {
                color = cYellow
        }
        s.out.Printf("context  %s%d%% used%s of %s tokens\n", color, pct, cReset, fmtTokens(cw))
        s.out.Printf("messages %d   turns %d\n", s.Runner.Messages(), s.Runner.Turns())
        if pct >= 60 {
                fmt.Println("run /compact to free context")
        }
}

func (s *Session) cmdDiff(ctx context.Context) {
        out, err := s.localOutput(ctx, []string{"diff", "--stat"}, 60)
        if err != nil {
                fmt.Println("not a git repository (or no git)")
                return
        }
        if strings.TrimSpace(out) == "" {
                fmt.Println("no changes")
                return
        }
        fmt.Print(out)
        if full, err := s.localOutput(ctx, []string{"diff"}, 60); err == nil && strings.TrimSpace(full) != "" {
                s.out.Printf("%s%s%s", renderDiff(full), "\n", "")
        }
}

func (s *Session) cmdInit() {
        root := s.Workspace
        var lines []string
        lines = append(lines, "# "+filepath.Base(root), "")
        if b := gitBranch(root); b != "" {
                lines = append(lines, "git branch: "+b, "")
        }
        // Detect project manifests the way /init does in Claude Code,
        // but statically: no model call, instant and deterministic.
        detected := 0
        for _, probe := range []struct {
                file  string
                note  string
                cmds  string
        }{
                {"go.mod", "Go module", "go build ./... · go test ./..."},
                {"package.json", "Node.js", "npm install · npm run build"},
                {"requirements.txt", "Python (pip)", "pip install -r requirements.txt"},
                {"pyproject.toml", "Python (pyproject)", "pip install -e ."},
                {"Cargo.toml", "Rust", "cargo build · cargo test"},
                {"pom.xml", "Maven", "mvn package"},
                {"composer.json", "PHP (Composer)", "composer install"},
                {"Makefile", "Make", "make"},
        } {
                if _, err := os.Stat(filepath.Join(root, probe.file)); err == nil {
                        detected++
                        lines = append(lines, "- "+probe.note+" ("+probe.file+"): "+probe.cmds)
                }
        }
        if detected == 0 {
                lines = append(lines, "- no standard project manifest detected")
        }
        // top-level entries
        if entries, err := os.ReadDir(root); err == nil {
                var names []string
                for i, e := range entries {
                        if i >= 24 {
                                names = append(names, "…")
                                break
                        }
                        name := e.Name()
                        if e.IsDir() {
                                name += "/"
                        }
                        names = append(names, name)
                }
                lines = append(lines, "", "## Layout", strings.Join(names, "  "))
        }
        lines = append(lines, "", "## Notes",
                "- Prefer small, precise edits.", "- Ask before destructive operations.", "")
        content := strings.Join(lines, "\n")
        target := filepath.Join(root, "AURORA.md")
        if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
                fmt.Printf("%sinit failed:%s %v\n", cRed, cReset, err)
                return
        }
        fmt.Println("wrote", target, fmt.Sprintf("(%d manifests detected)", detected))
}

func (s *Session) cmdDoctor(ctx context.Context) {
        fmt.Println("aurora doctor")
        fmt.Println("  version   ", version.Short())
        fmt.Println("  model     ", s.Runner.Model())
        fmt.Println("  workspace ", s.Workspace)
        fmt.Println("  key       ", keyPresent())
        ctxT, cancel := context.WithTimeout(ctx, 2*time.Second)
        defer cancel()
        if _, err := updater.Check(ctxT, s.Cfg, version.Short(), nil); err != nil {
                fmt.Println("  update    ", err.Error())
        } else {
                fmt.Println("  update    channels reachable")
        }
}

func keyPresent() string {
        if v := os.Getenv("AURORA_API_KEY"); v != "" {
                return "AURORA_API_KEY set"
        }
        if v := os.Getenv("OPENAI_API_KEY"); v != "" {
                return "OPENAI_API_KEY set"
        }
        if p, err := config.Dir(); err == nil {
                if _, err := os.Stat(filepath.Join(p, ".env")); err == nil {
                        return "key store present"
                }
        }
        return "missing (AURORA_API_KEY or OPENAI_API_KEY)"
}

// runLocalShell executes `!cmd` directly, bypassing the agent —
// the Claude Code `!` passthrough. Output goes straight to the terminal.
func (s *Session) runLocalShell(ctx context.Context, command string) {
        command = strings.TrimSpace(command)
        if command == "" {
                return
        }
        var cmd *exec.Cmd
        if runtime.GOOS == "windows" {
                shell := s.Cfg.Agent.Shell
                if strings.TrimSpace(shell) == "" {
                        shell = "powershell"
                }
                if strings.EqualFold(filepath.Base(shell), "powershell") || strings.EqualFold(filepath.Base(shell), "pwsh") {
                        cmd = exec.CommandContext(ctx, shell, "-NoProfile", "-Command", command)
                } else {
                        cmd = exec.CommandContext(ctx, shell, "-c", command)
                }
        } else {
                cmd = exec.CommandContext(ctx, "sh", "-c", command)
        }
        cmd.Dir = s.Workspace
        cmd.Stdout = os.Stdout
        cmd.Stderr = os.Stderr
        cmd.Stdin = os.Stdin
        if err := cmd.Run(); err != nil {
                fmt.Printf("%s!%s %v\n", cRed, cReset, err)
        }
}

// localOutput runs a git command in the workspace and captures stdout.
func (s *Session) localOutput(ctx context.Context, args []string, timeout time.Duration) (string, error) {
        c, cancel := context.WithTimeout(ctx, timeout)
        defer cancel()
        cmd := exec.CommandContext(c, "git", args...)
        cmd.Dir = s.Workspace
        out, err := cmd.Output()
        return string(out), err
}

// leftover small reader helper used by tests
func readLine(r *bufio.Reader) string {
        line, _ := r.ReadString('\n')
        return strings.TrimSpace(line)
}
