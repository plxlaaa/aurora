# Requirements Document

## Introduction

Aurora v0.2.0 added spinner, tool cards, diffs, and usage tracking. Benchmarking against source-level analysis of Claude Code's REPL and screenshot analysis of OpenAI Codex CLI, the interface still lacks polish: a plain unboxed banner, tool dots without state transitions, no per-turn summary, no context-window warning, and flat help text. This specification upgrades the REPL to match the visual language of both products while keeping the zero-dependency rule.

## Glossary

- **Rich mode**: stdout and stdin are character devices; all styling enabled
- **Tool dot**: The `⏺` marker on a tool card; states are running (cyan), success (green), failure (red)
- **Card rewind**: Erasing the tool card line with `\033[1A\033[2K` and reprinting it with the final dot color
- **Turn summary**: One dim line after a successful turn showing token delta and elapsed time
- **Context watermark**: Percentage of the configured context window consumed by the last request's prompt tokens
- **Rounded banner**: A `╭─╮ │ ╰─╯` box around the title line, label/value rows outside the box

## Requirements

### Requirement 1

**User Story:** AS a user, I want a polished welcome banner, so that the session start communicates version, model, and workspace at a glance.

#### Acceptance Criteria

1. WHEN rich mode starts, THE Aurora SHALL print a rounded-corner box around the title line `✻ Aurora <version> · terminal coding agent`
2. WHEN the banner renders, THE Aurora SHALL print label/value rows for model, provider, git branch, and workspace with dim labels
3. WHEN the workspace is a git repository, THE Aurora SHALL show the current branch read from `.git/HEAD`; otherwise THE Aurora SHALL omit the git row
4. WHEN the banner renders in rich mode, THE Aurora SHALL append a rotating example task hint (`Try: "..."`) and the `/help` tip
5. WHEN stdout is a pipe, THE Aurora SHALL print the plain multi-line banner without box-drawing characters

### Requirement 2

**User Story:** AS a user, I want tool call dots to reflect execution state, so that I can see at a glance which tools succeeded.

#### Acceptance Criteria

1. WHEN a tool card is printed while running, THE dot SHALL be cyan
2. WHEN the tool finishes, THE Aurora SHALL rewrite the card line with a green dot on success or a red dot on failure, followed by the result line
3. WHEN rewriting the card, THE Aurora SHALL truncate the card to the detected terminal width so the rewind stays on one line
4. WHEN terminal width detection fails, THE Aurora SHALL assume 80 columns
5. WHEN output intervenes between card and result (shell confirm prompt, streamed deltas), THE Aurora SHALL skip the rewind and print the result line only
6. WHEN the result line is printed, THE `⎿` mark SHALL be green on success and red on failure

### Requirement 3

**User Story:** AS a user, I want per-turn feedback, so that I know what a turn cost and when context is running low.

#### Acceptance Criteria

1. WHEN a turn completes successfully, THE Aurora SHALL print one dim summary line with token delta (prompt up / completion down) and elapsed seconds
2. WHEN the last request's prompt tokens exceed 60% of the configured context window, THE summary line SHALL append a yellow `context NN% used, /compact` segment
3. WHEN the prompt tokens exceed 80%, THE context segment SHALL be red
4. WHEN no usage was reported or the window is unset, THE summary SHALL show `n/a` tokens and omit the context segment
5. WHEN the user runs `/status`, THE output SHALL include the context percentage and window size

### Requirement 4

**User Story:** AS a user, I want the loading animation and help to feel like Claude Code, so that the terminal experience is familiar.

#### Acceptance Criteria

1. WHEN the spinner runs, THE frames SHALL cycle `· ✢ ✳ ∗ ✻ ✽` forward then reverse at 120 ms
2. WHEN the user runs `/help`, command names SHALL be bold and descriptions dim, one per line
3. WHEN the user runs `/model` without arguments, THE Aurora SHALL print the current model
4. WHEN the context window is configured, `aurora config set llm.context_window <n>` SHALL persist it
5. WHEN the implementation ships, THE Aurora SHALL add zero go.mod dependencies and bump the version to 0.3.0
