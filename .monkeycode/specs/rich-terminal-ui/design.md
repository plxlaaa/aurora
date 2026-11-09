# Technical Design: Rich Terminal UI

## Overview

Upgrade the REPL to a Codex/Claude Code style experience while keeping the zero-dependency rule. All rendering stays in `internal/tui`; the agent and llm packages gain additive events and usage data; the tools package gains diff production that is excluded from LLM context.

## Architecture

```
Runner.Run(ctx, input)
  emits: step → delta* → tool → tool_result(+Diff) → done | error
  returns: (final, Usage, error)
tui.Session
  output lock (sync.Mutex)  ── spinner goroutine writes through it
  event handler             ── markdown-lite line buffer, tool cards, diffs
  per-turn cancel ctx       ── signal.NotifyContext(os.Interrupt)
```

## Module Changes

### internal/llm

- Add `Usage struct { Prompt, Completion, Total int64 }` with `json` tags `prompt_tokens`, `completion_tokens`, `total_tokens`.
- `chatRequest` gains `StreamOptions *struct{ IncludeUsage bool }` (`stream_options`), set only when `Stream: true`.
- `streamChunk` gains a top-level `Usage` field; ChatStream accumulates the last non-zero usage seen (providers send it in the final chunk).
- `ChatStream` signature becomes `(ctx, msgs, tools, onDelta) (Message, Usage, error)`.
- `chatResponse` gains a `usage` field; `Chat` returns `(Message, Usage, error)`.
- Zero usage stays zero; no error is raised.

### internal/tools

- `Result` gains `Diff string \`json:"-"\`` so the LLM payload stays `{"ok","content"}`.
- `editFile`: read source before replace, compute line diff of `src` vs `out`, attach to Result.
- `writeFile`: if the file exists, diff old vs new; if new, attach a synthetic `+` block (first lines only) with a `created` marker.
- New `internal/tools/diff.go`: line-based LCS diff (standard DP, capped inputs at ~2000 lines to bound memory), returns unified-style lines prefixed `-`/`+`/` ` with 2 lines of context, capped at 60 output lines.
- Registry methods that need diffs capture them before mutation.

### internal/agent

- `Event` gains `Usage Usage` and `Diff string` fields; existing kinds unchanged.
- New event kinds: `step` (before each LLM call, `Text` = step index), `turn_end` (after the loop, on success and on error).
- `Runner` gains `Usage Usage` (session totals) and `Last Usage` via a `SessionUsage()`/`LastUsage()` accessor pair; each step's usage is added inside `Run`.
- `Run` returns `(string, Usage, error)` — the step usage delta of this turn (already included in session totals).
- Tool result events carry `Diff` from the tools Result.

### internal/tui

New files:

- `output.go`: `type out struct` with `sync.Mutex`, `Print`/`Printf` methods; replaces direct `fmt.Print` in the REPL paths. Exposes `ClearLine()` (writes `\r\033[2K`).
- `spinner.go`: `type spinner struct` — goroutine ticking every 120 ms; frames `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`; verb pool of ~40 words ("Thinking", "Analyzing", "Brewing", "Pondering", "Refactoring", ...), re-rolled every 8 s; line format `\r\033[2K<frame> <verb>... <elapsed>s · Ctrl+C interrupt`; `Start(w *out)`, `Stop()` (clears line, joins goroutine); a no-op when `!IsTTY()`.
- `markdown.go`: `renderLine(line string, state *mdState) string` — tracks fenced-code state; applies heading bold/cyan, bullet `•`, inline code cyan, bold segments; fenced lines dim + 2-space indent; state struct carried across calls; `plain` build for non-TTY returns input unchanged (except bullet normalization disabled too).
- `repl.go` (rewrite of the event handler):
  - `Session` gains `usage` totals mirror, `out *out`, `spin *spinner`.
  - `runTurn(line)`: builds `ctx, cancel := signal.NotifyContext(base, os.Interrupt)`; defers cancel; prints prompt-side newline; calls `Runner.Run`; spinner starts on `step`, stops on first `delta`/`tool`/`tool_result`; on interrupt error prints `turn cancelled`; on success prints nothing extra (final newline already handled).
  - Tool card: parse `ev.Name` + primary arg from `ev.Text` JSON (`path` → `pattern` → `command` → first key); display names `Read/Write/Edit/List/Glob/Grep/Bash`.
  - Result line: `+ ok` dim summary derived from content (count lines), or red first line on failure; diff block printed colored, capped 40 lines + `... +N more`.
  - `/usage`: prints session totals and last-step values from Runner accessors.
  - `/status`: extended with turns, messages, tokens.
  - Ctrl+C idle: signal handler installed for the whole REPL; when a turn is running the turn-scoped handler wins (fresh NotifyContext each turn, stop() on exit restores default handler for exit path); idle Ctrl+C exits with a newline.
- `Banner`: unchanged content, printed through the lock.

### cmd/aurora/main.go

- Task mode (`aurora "<task>"`) prints tool cards via the same handler code path (shared `attach` on Session or a standalone handler func); spinner enabled there too.

## Data Flow (tool diff)

1. `editFile` computes `diff.Lines(src, out)` → `Result.Diff`.
2. `agent.Run` copies `res.Diff` into the `tool_result` event; LLM message keeps `{"ok","content"}`.
3. `tui` renders the diff under the card.

## Error Handling

- Spinner panics impossible: goroutine owns only its line; `Stop` uses a done channel with timeout guard.
- Provider without usage support: fields stay zero, `/usage` prints `n/a`.
- Interrupt mid-stream: `ChatStream` returns ctx error; `Run` returns error; REPL prints `turn cancelled` and keeps accumulated messages (partial assistant message is dropped by not appending — matches current behavior of returning before append).

## Testing

- `internal/llm`: httptest server asserting `stream_options` present and usage parsed from final chunk; non-stream usage parse.
- `internal/tools`: diff unit tests (equal, insert, delete, modify, cap), write-overwrites-existing diff, edit diff attached, Result JSON marshals without diff.
- `internal/agent`: fake client (existing test pattern) asserts usage accumulation, `step`/`turn_end` events, diff propagation.
- `internal/tui`: markdown-lite table tests (headings, bullets, bold, inline code, fence state); spinner start/stop smoke with fake writer; tool card formatting tests.
- Manual: `go build && ./aurora` interactive smoke — spinner during think, cards on edits, Ctrl+C cancel, `/usage`.

## Version

- `internal/version/version.go`, `Makefile`, `scripts/pack.sh`: 0.2.0.
