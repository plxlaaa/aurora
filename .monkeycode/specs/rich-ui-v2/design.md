# Technical Design: Rich UI v2

## Overview

Polish pass over the v0.2 REPL based on Claude Code source-map analysis (welcome box, `⏺` dot states, `⎿` result marks, `·✢✳∗✻✽` spinner, context watermark) and Codex CLI screenshot analysis (session info rows, git branch, key hints). Zero dependencies maintained.

## Module Changes

### internal/config

- `LLMConfig` gains `ContextWindow int64 \`json:"context_window"\``; `Defaults()` sets 128000.
- `SetField` accepts `llm.context_window` / `context_window` (positive integer).
- No heal migration needed (new field).

### internal/tui/term.go (new)

- `termWidth() int` returns stdout columns.
- `!windows`: `syscall.Syscall(SYS_IOCTL, fd, TIOCGWINSZ, &winsize{})`, fallback 80.
- `windows`: `kernel32.GetStdHandle(STD_OUTPUT_HANDLE)` + `GetConsoleScreenBufferInfo`, `srWindow.Right-Left+1`, fallback 80.

### internal/tui/banner.go (new)

- `Banner(p *printer, cfg, workspace, versionStr)`:
  - rich: rounded box `╭─…─╮ / │ ✻ Aurora v0.3.0 · terminal coding agent │ / ╰─…─╯`; box width = max(title width, 44) + 2 padding; then dim rows `model/provider/git/workspace`; then dim `Try: "..."` rotating hint + `/help for commands, Ctrl+C to exit`.
  - plain (pipe): existing 4-line output.
- `gitBranch(root string) string`: read `<root>/.git/HEAD`; parse `ref: refs/heads/<name>`; `gitdir:` pointers and unreadable files return `""`.

### internal/tui/spinner.go

- Frames `· ✢ ✳ ∗ ✻ ✽` bounced (0→5→0), 120 ms tick unchanged, verb reroll unchanged.

### internal/tui/cards.go

- `toolCard(name, args, dot string)`: dot passed by caller (`cCyan` running, `cGreen` ok, `cRed` fail).
- `truncatePlain(s string, w int)`: rune-safe truncation to card width limit `termWidth()-4`.
- Result mark coloring handled at call site.

### internal/tui/repl.go

- Session gains `pendingCard string` (the rendered card line) + `turnStart time.Time`.
- attach `tool`: render card with cyan dot truncated, store in `pendingCard`, print `\n<card>\n`.
- attach `tool_result`: if `pendingCard != ""` → print `\033[1A\033[2K<card with final dot>\n`, clear pendingCard; else print nothing extra; then result line with green/red `⎿` and diff as before.
- `pendingCard` cleared on: delta, shell confirm callback (set in `rebuild` closure before printing `run shell?`), turn start.
- `runTurn`: record start time; on success print dim summary `↑prompt ↓completion tokens · N.Ns` + optional context segment; on cancel/error unchanged.
- Context watermark: `pct = LastUsage().Prompt * 100 / cfg.LLM.ContextWindow` when Prompt > 0 and ContextWindow > 0; ≥60 yellow, ≥80 red.
- `Help(p *printer)` restyled via `replCommands` slice (bold name, dim desc).
- `/model` (no arg) prints current model; `/status` adds `context NN% of 128k`.

## Data Flow

```
tool event → card(cyan) stored → tool_result event → rewind+recolor card → ⎿ line → diff
turn end → summary(pct watermark)
```

## Error Handling

- Rewind guarded by `pendingCard`; any intervening output clears it (no cursor corruption).
- termWidth failures return 80; box drawing only in rich mode.
- git branch failures return empty; row omitted.

## Testing

- `gitBranch` with temp git dir (ref, detached, missing).
- card truncation, banner plain vs rich, spinner frame bounce, watermark color thresholds.
- Full build/vet/test + pty smoke (mock LLM): verify box, card recolor, summary line.

## Version

- 0.3.0 in version.go, Makefile, pack.sh.
