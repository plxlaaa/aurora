package tui

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/plxlaaa/aurora/internal/config"
	"github.com/plxlaaa/aurora/internal/version"
)

const bannerMinWidth = 44

var tryPrompts = []string{
	`explain this codebase`,
	`find and fix the failing tests`,
	`write tests for internal/tools`,
	`refactor the busiest file you find`,
	`add a /diff command to the REPL`,
}

func bannerRows(cfg config.Config, workspace string) [][2]string {
	rows := [][2]string{
		{"model", cfg.LLM.Model},
		{"provider", cfg.LLM.Provider},
	}
	if b := gitBranch(workspace); b != "" {
		rows = append(rows, [2]string{"git", b})
	}
	rows = append(rows, [2]string{"workspace", workspace})
	return rows
}

func tryHint() string {
	idx := int(time.Now().UnixNano()/int64(time.Hour)) % len(tryPrompts)
	return tryPrompts[idx]
}

func Banner(p *printer, cfg config.Config, workspace string) {
	if !IsTTY() {
		p.Printf("%s%s%s  terminal coding agent\n", cBold, cCyan, version.Short())
		for _, row := range bannerRows(cfg, workspace) {
			p.Printf("%s%s%s %s\n", cDim, row[0], cReset, row[1])
		}
		p.Printf("%stype a task, /help for commands, Ctrl+C to exit%s\n\n", cDim, cReset)
		return
	}
	title := fmt.Sprintf("✻ Aurora %s · terminal coding agent", version.Short())
	inner := len([]rune(title)) + 2
	if inner < bannerMinWidth {
		inner = bannerMinWidth
	}
	pad := inner - 2 - len([]rune(title))
	p.Printf("%s╭%s╮%s\n", cCyan, strings.Repeat("─", inner), cReset)
	p.Printf("%s│%s %s%s%s%s %s│%s\n",
		cCyan, cReset,
		cBold, title, cReset,
		strings.Repeat(" ", pad),
		cCyan, cReset)
	p.Printf("%s╰%s╯%s\n", cCyan, strings.Repeat("─", inner), cReset)
	for _, row := range bannerRows(cfg, workspace) {
		val := row[1]
		if row[0] == "workspace" {
			val = truncateRunes(val, termWidth()-16)
		}
		p.Printf("  %s%-10s%s %s\n", cDim, row[0], cReset, val)
	}
	p.Printf("\n  %sTry: %q · /help for commands · Ctrl+C to exit%s\n\n", cDim, tryHint(), cReset)
}

func truncateRunes(s string, max int) string {
	if max <= 0 {
		return s
	}
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	if max <= 1 {
		return string(r[:1])
	}
	return string(r[:max-1]) + "…"
}

func gitBranch(root string) string {
	if root == "" {
		return ""
	}
	raw, err := os.ReadFile(filepath.Join(root, ".git", "HEAD"))
	if err != nil {
		return ""
	}
	head := strings.TrimSpace(string(raw))
	if strings.HasPrefix(head, "ref: ") {
		return strings.TrimPrefix(strings.TrimPrefix(head, "ref: "), "refs/heads/")
	}
	if len(head) >= 7 {
		return "detached"
	}
	return ""
}
