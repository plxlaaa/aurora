# Windows Terminal Agent Framework

Feature Name: windows-terminal-agent
Updated: 2026-11-08

## Description

Native Go CLI with OpenAI-compatible agent loop, Windows installer, and racing self-update.

## Architecture

See 当前工作区 `/.monkeycode/docs/ARCHITECTURE.md`.

## Components and Interfaces

- CLI in `cmd/aurora`
- Agent in `internal/agent`
- Updater in `internal/updater`
- Installer in `scripts/install.ps1`

## Data Models

`config.Config`, `updater.Manifest`, `llm.Message`.

## Correctness Properties

- Workspace tools cannot escape the root directory
- Update replacement never installs a checksum-mismatched payload when SHA-256 is present
- Channel race returns the first successful complete download

## Error Handling

Failed mirrors are logged as `channel_fail` and ignored if another channel wins. Shell tools require confirmation by default.

## Test Strategy

Unit tests for version compare, channel selection, race download, zip extract, workspace path guard.

## References

Installer and updater source under `scripts/` and `internal/updater/`.
