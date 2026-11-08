# Requirements Document

## Introduction

Aurora is a Windows-first terminal coding agent. This specification covers the v0.1 framework: install, REPL/agent loop, and local multi-channel self-update.

## Glossary

- **Aurora**: The CLI binary and agent runtime
- **Channel**: A download mirror wrapping the same GitHub release URL
- **Manifest**: JSON describing versioned assets and checksums
- **Workspace**: Directory from which Aurora is launched

## Requirements

### Requirement 1

**User Story:** AS a Windows developer, I want a one-line installer, so that I can start using Aurora without Node or Python.

#### Acceptance Criteria

1. WHEN the user runs the PowerShell installer, THE Aurora SHALL copy `aurora.exe` into `%LOCALAPPDATA%\Programs\Aurora`
2. WHEN installation succeeds, THE Aurora SHALL append that directory to the user PATH
3. IF a download mirror fails, THE installer SHALL continue with remaining mirrors until one payload succeeds

### Requirement 2

**User Story:** AS a developer, I want a terminal agent that can read and edit my repo, so that I can complete coding tasks without leaving the shell.

#### Acceptance Criteria

1. WHEN the user starts `aurora` with no arguments, THE Aurora SHALL open an interactive REPL
2. WHEN the user provides a task, THE Aurora SHALL call an OpenAI-compatible chat API with workspace tools
3. IF a tool path resolves outside the workspace, THE Aurora SHALL reject the call

### Requirement 3

**User Story:** AS a user on an unstable GitHub link, I want local updates through multiple channels, so that I can upgrade without a store.

#### Acceptance Criteria

1. WHEN the user runs `aurora update`, THE Aurora SHALL fetch the latest manifest from mirrored URLs in parallel
2. WHEN a newer version exists, THE Aurora SHALL race-download the matching platform zip and replace the running binary
3. IF the manifest includes SHA-256, THE Aurora SHALL verify the payload before replacement
