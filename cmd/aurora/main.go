package main

import (
        "bufio"
        "context"
        "encoding/json"
        "fmt"
        "io"
        "os"
        "strings"

        "github.com/plxlaaa/aurora/internal/agent"
        "github.com/plxlaaa/aurora/internal/config"
        "github.com/plxlaaa/aurora/internal/setup"
        "github.com/plxlaaa/aurora/internal/tui"
        "github.com/plxlaaa/aurora/internal/updater"
        "github.com/plxlaaa/aurora/internal/version"
)

func main() {
        updater.FinalizeReplacement()
        finishPendingUpdate()
        if err := run(os.Args[1:]); err != nil {
                fmt.Fprintln(os.Stderr, "aurora:", err)
                os.Exit(1)
        }
}

func finishPendingUpdate() {
        exe, err := os.Executable()
        if err != nil {
                return
        }
        _ = updater.ApplyPending(exe)
}

func run(args []string) error {
        if len(args) == 0 {
                return cmdREPL(nil)
        }
        switch args[0] {
        case "help", "-h", "--help":
                printHelp()
                return nil
        case "version", "-v", "--version":
                fmt.Println(version.String())
                return nil
        case "init":
                return cmdInit()
        case "setup":
                return cmdSetup()
        case "config":
                return cmdConfig(args[1:])
        case "update":
                return cmdUpdate(args[1:])
        case "doctor":
                return cmdDoctor()
        case "run", "-p", "--print":
                return cmdRun(strings.Join(args[1:], " "))
        default:
                if strings.HasPrefix(args[0], "-") {
                        return fmt.Errorf("unknown flag %s", args[0])
                }
                return cmdREPL(args)
        }
}

func printHelp() {
        fmt.Print(`Aurora - high-performance terminal coding agent

Usage:
  aurora                 interactive REPL
  aurora <task>          run one task then exit
  aurora run <task>      same as above
  aurora init            write default config
  aurora setup           interactive first-run config wizard
  aurora config          show config path and values
  aurora config set k v  set a config field and save
  aurora update          download latest binary via racing mirrors
  aurora update --check  only check remote version
  aurora doctor          diagnose install, PATH, and update channels
  aurora version         print version

Environment:
  AURORA_API_KEY         OpenAI-compatible API key
  AURORA_BASE_URL        override LLM base URL
  AURORA_MODEL           override model id

Install on Windows (PowerShell):
  irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex
`)
}

func loadCfg() (config.Config, error) {
        cfg, err := config.Ensure()
        if err != nil {
                return cfg, err
        }
        applyEnvOverrides(&cfg)
        return cfg, nil
}

func applyEnvOverrides(cfg *config.Config) {
        if v := strings.TrimSpace(os.Getenv("AURORA_BASE_URL")); v != "" {
                cfg.LLM.BaseURL = v
        }
        if v := strings.TrimSpace(os.Getenv("AURORA_MODEL")); v != "" {
                cfg.LLM.Model = v
        }
}

func readStdinLine(reader *bufio.Reader) (string, bool) {
        line, err := reader.ReadString('\n')
        eof := err == io.EOF
        return strings.TrimSpace(line), eof
}

func runSetupWizard(reader *bufio.Reader) (config.Config, error) {
        cfg, err := config.Load()
        if err != nil {
                return cfg, err
        }
        res, err := setup.Run(setup.Options{In: reader, Out: os.Stdout, Cfg: cfg})
        if err != nil {
                return cfg, err
        }
        if err := config.Save(res.Config); err != nil {
                return cfg, err
        }
        if res.KeyName != "" && res.KeyValue != "" {
                if err := config.SaveKeyStore(res.KeyName, res.KeyValue); err != nil {
                        return cfg, err
                }
        }
        cfgPath, _ := config.Path()
        keyPath, _ := config.KeyStorePath()
        setup.PrintSummary(os.Stdout, res, cfgPath, keyPath)
        return res.Config, nil
}

func cmdSetup() error {
        _, err := runSetupWizard(bufio.NewReader(os.Stdin))
        return err
}

func offerFirstRunSetup(cfg config.Config, reader *bufio.Reader) (config.Config, error) {
        exists, err := config.Exists()
        if err != nil || exists {
                return cfg, nil
        }
        if strings.TrimSpace(os.Getenv("AURORA_API_KEY")) != "" || strings.TrimSpace(os.Getenv("OPENAI_API_KEY")) != "" {
                return cfg, nil
        }
        if !tui.IsInteractiveTTY() {
                return cfg, nil
        }
        fmt.Print("aurora: no config found yet. run setup now? [Y/n] ")
        line, eof := readStdinLine(reader)
        if eof && line == "" {
                return cfg, nil
        }
        switch strings.ToLower(line) {
        case "", "y", "yes":
                return runSetupWizard(reader)
        default:
                fmt.Println("skipping setup; run 'aurora setup' any time")
                return cfg, nil
        }
}

func cmdInit() error {
        cfg, err := config.Ensure()
        if err != nil {
                return err
        }
        p, _ := config.Path()
        fmt.Println("wrote", p)
        fmt.Println("model", cfg.LLM.Model)
        return nil
}

func cmdConfig(args []string) error {
        cfg, err := loadCfg()
        if err != nil {
                return err
        }
        p, _ := config.Path()
        if len(args) >= 3 && args[0] == "set" {
                if err := config.SetField(&cfg, args[1], strings.Join(args[2:], " ")); err != nil {
                        return err
                }
                if err := config.Save(cfg); err != nil {
                        return err
                }
                fmt.Println("updated", args[1], "in", p)
                return nil
        }
        fmt.Println("path:", p)
        raw, _ := json.MarshalIndent(cfg, "", "  ")
        fmt.Println(string(raw))
        return nil
}

func cmdDoctor() error {
        cfg, err := loadCfg()
        if err != nil {
                return err
        }
        exe, _ := os.Executable()
        cfgPath, _ := config.Path()
        install, _ := config.InstallDir()
        fmt.Println("aurora doctor")
        fmt.Println("  version   ", version.String())
        fmt.Println("  executable", exe)
        fmt.Println("  install   ", install)
        fmt.Println("  config    ", cfgPath)
        fmt.Println("  model     ", cfg.LLM.Model)
        fmt.Println("  base_url  ", cfg.LLM.BaseURL)
        if config.APIKey(cfg) == "" {
                fmt.Println("  api_key    missing (" + cfg.LLM.APIKeyEnv + ", key store, or OPENAI_API_KEY)")
        } else {
                fmt.Println("  api_key    present")
        }
        if kp, err := config.KeyStorePath(); err == nil {
                store, _ := config.LoadKeyStore()
                if strings.TrimSpace(store[cfg.LLM.APIKeyEnv]) != "" {
                        fmt.Println("  key_store ", kp, "("+cfg.LLM.APIKeyEnv+" set)")
                } else {
                        fmt.Println("  key_store ", kp, "(empty)")
                }
        }
        if exe != "" {
                if st, err := os.Stat(updater.NewPath(exe)); err == nil && !st.IsDir() {
                        fmt.Println("  pending    ", updater.NewPath(exe))
                }
        }
        fmt.Println("  mirrors")
        for _, ch := range updater.Select(cfg.Update.Mirrors) {
                fmt.Println("   -", ch.Name)
        }
        return nil
}

func cmdUpdate(args []string) error {
        cfg, err := loadCfg()
        if err != nil {
                return err
        }
        checkOnly := false
        force := false
        for _, a := range args {
                if a == "--check" {
                        checkOnly = true
                }
                if a == "--force" {
                        force = true
                }
        }
        ctx := context.Background()
        progress := func(event, detail string) {
                fmt.Printf("  [%s] %s\n", event, detail)
        }
        if checkOnly {
                res, err := updater.Check(ctx, cfg, version.Short(), progress)
                if err != nil {
                        return err
                }
                fmt.Println(res.Message)
                return nil
        }
        res, err := updater.CheckAndApply(ctx, cfg, version.Short(), force, progress)
        if err != nil {
                return err
        }
        fmt.Println(res.Message)
        if res.Updated && res.Channel != "" {
                fmt.Printf("downloaded via %s in %s\n", res.Channel, res.Elapsed)
        }
        return nil
}

func cmdRun(prompt string) error {
        prompt = strings.TrimSpace(prompt)
        if prompt == "" {
                return fmt.Errorf("empty prompt")
        }
        cfg, err := config.Load()
        if err != nil {
                return err
        }
        applyEnvOverrides(&cfg)
        wd, _ := os.Getwd()
        reader := bufio.NewReader(os.Stdin)
        cfg, err = offerFirstRunSetup(cfg, reader)
        if err != nil {
                return err
        }
        r, err := agent.New(cfg, wd, func(cmd string) bool {
                if !cfg.Agent.ConfirmShell {
                        return true
                }
                return tui.Confirm(reader, cmd)
        })
        if err != nil {
                return err
        }
        r.On = func(ev agent.Event) {
                switch ev.Kind {
                case "delta":
                        fmt.Print(ev.Text)
                case "tool":
                        fmt.Printf("\n▸ %s\n", ev.Name)
                case "tool_result":
                        color := "ok"
                        if !ev.OK {
                                color = "failed"
                        }
                        fmt.Printf("  (%s)\n", color)
                }
        }
        _, _, err = r.Run(context.Background(), prompt)
        fmt.Println()
        return err
}

func cmdREPL(args []string) error {
        wd, _ := os.Getwd()
        resume := tui.ResumeAsk
        lineMode := false
        var task string
        for _, a := range args {
                switch a {
                case "--resume", "--continue", "-c":
                        resume = tui.ResumeYes
                case "--no-resume":
                        resume = tui.ResumeNo
                case "--line":
                        lineMode = true
                default:
                        if task == "" {
                                task = a
                        }
                }
        }
        if task != "" {
                return cmdRun(task)
        }
        cfg, err := config.Load()
        if err != nil {
                return err
        }
        applyEnvOverrides(&cfg)
        reader := bufio.NewReader(os.Stdin)
        cfg, err = offerFirstRunSetup(cfg, reader)
        if err != nil {
                return err
        }
        // interactive terminal -> fullscreen TUI (fixed composer + status
        // bar); pipes and --line stay on the classic scrolling REPL
        if !lineMode && tui.IsInteractiveTTY() {
                runner, err := agent.New(cfg, wd, nil)
                if err != nil {
                        return err
                }
                if resume == tui.ResumeYes {
                        _, _ = agent.LoadSession(cfg, wd, runner)
                }
                if err := tui.RunScreen(runner, wd, version.Short(), cfg.LLM.ContextWindow); err != nil {
                        return err
                }
                _ = agent.SaveSession(cfg, wd, runner)
                return nil
        }
        return tui.RunREPL(context.Background(), cfg, wd, reader, func() error {
                return cmdUpdate(nil)
        }, resume)
}
