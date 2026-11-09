# Requirements Document

## Introduction

Aurora's REPL output is plain and silent: assistant text streams raw, tool calls print raw JSON arguments, file edits print a one-line summary, and nothing moves on screen while the model is thinking. Benchmarking against Codex CLI and Claude Code, this specification upgrades the terminal experience to a rich, single-port, zero-dependency UI: activity spinner with rotating verbs and elapsed time, Ctrl+C turn cancellation, one-line tool cards with colored diffs, line-based markdown-lite rendering of assistant replies, and token usage tracking surfaced through `/status` and `/usage`.

## Glossary

- **Turn**: One `Runner.Run` execution, from the user pressing Enter to the final answer or error
- **Step**: One LLM request/response cycle inside a turn; a turn has one or more steps
- **Spinner**: A single animated status line (braille frame + verb + elapsed seconds + interrupt hint)
- **Tool card**: The one-line summary printed when a tool call starts, e.g. `Read src/foo.go`
- **Result line**: The compact `+ok/-fail` line printed when a tool finishes
- **Diff block**: Colored `-`/`+` line pairs rendered under edit/write tool cards
- **Usage**: Prompt tokens, completion tokens, and total tokens reported by the OpenAI-compatible endpoint for one request
- **Session usage**: The sum of usage across all steps since the last `/reset`
- **Interactive session**: stdin and stdout are both character devices
- **Markdown-lite**: Line-oriented rendering subset (headings, bold, inline code, bullets, code fences) applied to completed lines only

## Requirements

### Requirement 1

**User Story:** AS a user, I want a visible activity indicator while the model works, so that I know Aurora is running and how long it has been thinking.

#### Acceptance Criteria

1. WHEN a turn starts or a new step begins without pending streamed output, THE Aurora SHALL display a spinner line with a braille frame, a rotating verb, elapsed seconds, and the hint `Ctrl+C interrupt`
2. WHEN the spinner is active, THE Aurora SHALL advance the braille frame at a fixed interval and re-roll the verb after a fixed period
3. WHEN the first delta, tool card, or tool result event arrives, THE Aurora SHALL erase the spinner line before printing anything else
4. WHEN the turn ends, THE Aurora SHALL ensure the spinner is stopped
5. WHEN stdout is an interactive session, THE Aurora SHALL render the spinner; otherwise THE Aurora SHALL print nothing for the spinner

### Requirement 2

**User Story:** AS a user, I want Ctrl+C to cancel the running turn, so that a runaway task stops without killing the session.

#### Acceptance Criteria

1. WHEN Ctrl+C is pressed while a turn is running, THE Aurora SHALL cancel the turn context and print a `turn cancelled` notice with the partial answer kept in the transcript
2. WHEN Ctrl+C is pressed while the prompt is idle, THE Aurora SHALL exit the REPL
3. WHEN the cancelled step produced no content and no tool calls, THE Aurora SHALL surface the cancellation as an error notice instead of a final answer

### Requirement 3

**User Story:** AS a user, I want tool calls shown as compact cards, so that I can follow what Aurora does without reading raw JSON.

#### Acceptance Criteria

1. WHEN a tool call starts, THE Aurora SHALL print a one-line card with the tool display name and the primary argument (path, pattern, or command), truncated to one terminal line
2. WHEN a tool finishes successfully, THE Aurora SHALL print a dim result line summarizing the outcome (line counts for reads, replacement counts for edits, exit status hints for shell)
3. WHEN a tool finishes with a failure, THE Aurora SHALL print the failure reason in red on the result line
4. WHEN the tool is edit_file or write_file, THE Aurora SHALL render a diff block with `-` lines in red and `+` lines in green, capped at a fixed number of lines
5. WHEN the diff block exceeds the cap, THE Aurora SHALL print the first lines and a `... +N more` trailer
6. WHEN stdout is an interactive session, THE Aurora SHALL apply colors to cards and diffs; otherwise THE Aurora SHALL print plain text

### Requirement 4

**User Story:** AS a user, I want assistant replies rendered with light markdown, so that structure such as headings, code, and lists is readable.

#### Acceptance Criteria

1. WHEN an assistant delta completes a line during streaming, THE Aurora SHALL render that line with markdown-lite styling before printing
2. WHEN a line begins with `#`, `##`, or `###`, THE Aurora SHALL print it bold (and cyan for `#`)
3. WHEN a line begins with `-` or `*`, THE Aurora SHALL print it with a `•` bullet
4. WHEN a line contains inline `` `code` `` segments, THE Aurora SHALL print them in a distinct color
5. WHEN a line contains `**bold**` segments outside code spans, THE Aurora SHALL print them bold
6. WHEN a line is inside a fenced code block, THE Aurora SHALL print it dim and indented, and mark the fence lines with a dim rule
7. WHEN stdout is an interactive session, THE Aurora SHALL apply markdown-lite styling; otherwise THE Aurora SHALL print raw text

### Requirement 5

**User Story:** AS a user, I want token usage tracked and visible, so that I can watch consumption per request and per session.

#### Acceptance Criteria

1. WHEN the endpoint supports `stream_options.include_usage`, THE Aurora SHALL request usage in every streaming chat call and parse the usage from the final chunk
2. WHEN a non-stream fallback request completes, THE Aurora SHALL parse usage from the response when present
3. WHEN a step finishes, THE Aurora SHALL add its usage to the session totals
4. WHEN the user runs `/usage`, THE Aurora SHALL print prompt, completion, and total tokens for the session and for the last step
5. WHEN the user runs `/status`, THE Aurora SHALL include session turns, message count, and session total tokens alongside version, model, workspace, and yolo
6. WHEN the endpoint returns zero usage, THE Aurora SHALL keep counters at their previous values without errors

### Requirement 6

**User Story:** AS a maintainer, I want the rich UI confined to the tui package with no new dependencies, so that the zero-dependency build and Windows compatibility hold.

#### Acceptance Criteria

1. WHEN the UI upgrade is implemented, THE Aurora SHALL add zero entries to go.mod require directives
2. WHEN any goroutine prints output, THE Aurora SHALL serialize writes through one output lock shared with the spinner
3. WHEN the agent emits lifecycle events, THE Aurora SHALL keep event kinds additive so existing consumers (tests, future headless mode) keep compiling
4. WHEN the upgrade ships, THE Aurora SHALL bump the version to 0.2.0 in version.go, Makefile, and pack.sh
