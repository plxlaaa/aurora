# Requirements Document

## Introduction

Aurora ships with a bundled default config (OpenAI, gpt-4o-mini, `AURORA_API_KEY`). A new user who has no API key and no compatible endpoint hits an error on the first task. This specification adds a first-run setup wizard: a step-by-step interactive flow (modeled on Hermes Agent's `hermes setup`) plus automatic detection of a missing config file, so a new user can reach a working agent without hand-editing JSON.

## Glossary

- **Wizard**: The step-by-step interactive setup flow run by `aurora setup`
- **Provider preset**: A built-in named provider with a default base URL and suggested default model
- **Config directory**: `~/.config/aurora` on Linux, `%APPDATA%\aurora` on Windows
- **Key store file**: A dotenv-style file in the config directory that holds the API key value
- **First run**: Launch of Aurora when no config file exists at the standard config path

## Requirements

### Requirement 1

**User Story:** AS a new user, I want a step-by-step setup wizard, so that I can configure the model without hand-editing the config file.

#### Acceptance Criteria

1. WHEN the user runs `aurora setup`, THE Aurora SHALL start an interactive wizard that guides the user through provider, endpoint, model, and API key in separate steps
2. WHEN the wizard prompts for a provider, THE Aurora SHALL display the list of built-in provider presets plus a custom endpoint option
3. WHEN the user selects a provider preset, THE Aurora SHALL prefill that provider's default base URL
4. WHEN the user selects the custom endpoint option, THE Aurora SHALL prompt for a base URL
5. WHEN the user enters an empty value at a prompt that has a default, THE Aurora SHALL accept the default
6. WHEN all steps complete, THE Aurora SHALL save the collected values into the config file at the standard config path
7. WHEN the wizard saves the configuration, THE Aurora SHALL print a summary of the saved provider, base URL, and model

### Requirement 2

**User Story:** AS a first-time user, I want Aurora to offer configuration on the first launch, so that I hit a guided setup instead of an API key error.

#### Acceptance Criteria

1. WHEN the user starts `aurora` with no arguments or with a task and no config file exists at the standard config path, THE Aurora SHALL offer to run the setup wizard
2. WHEN the user accepts the offer, THE Aurora SHALL run the wizard before starting the REPL or the task
3. WHEN the user declines the offer, THE Aurora SHALL continue with the current default configuration
4. WHEN Aurora detects a non-interactive stdin, THE Aurora SHALL skip the offer and continue with default configuration
5. WHEN a config file exists, THE Aurora SHALL not repeat the offer

### Requirement 3

**User Story:** AS a user, I want the API key stored in a dedicated file, so that the key stays out of the config file and out of version control.

#### Acceptance Criteria

1. WHEN the user provides an API key in the wizard, THE Aurora SHALL write the key value into a key store file in the config directory
2. WHEN the wizard writes the key store file, THE Aurora SHALL set the file permissions to owner-only on platforms that support POSIX permissions
3. WHEN the wizard finishes, THE Aurora SHALL record the env var name of the key in `llm.api_key_env` of the config file
4. WHEN Aurora loads configuration, THE Aurora SHALL resolve the API key from the key store file before falling back to environment variables
5. WHEN the wizard prints its summary, THE Aurora SHALL mask the API key and reveal its first four characters only

### Requirement 4

**User Story:** AS a user of a domestic or self-hosted model service, I want built-in provider presets, so that I can pick a known-compatible endpoint with one keystroke.

#### Acceptance Criteria

1. THE Aurora SHALL provide built-in presets covering OpenAI, DeepSeek, Zhipu GLM, Moonshot Kimi, MiniMax, Ollama local, and a custom endpoint
2. WHEN the user selects a preset, THE Aurora SHALL suggest a default model for that preset
3. WHEN the user accepts the suggested model, THE Aurora SHALL store the suggested model in the config
4. WHEN the user enters a model name, THE Aurora SHALL store the entered model in the config
5. WHILE the wizard is active, THE Aurora SHALL allow the user to re-enter the current step's value

### Requirement 5

**User Story:** AS a user who mistypes an endpoint or key, I want immediate feedback, so that I do not discover the mistake after leaving the wizard.

#### Acceptance Criteria

1. WHEN the wizard finishes collecting values, THE Aurora SHALL offer a connection test step
2. WHEN the user accepts the connection test, THE Aurora SHALL send a minimal chat completion request to the configured endpoint with the configured key
3. WHEN the connection test succeeds, THE Aurora SHALL print a success message
4. WHEN the connection test fails, THE Aurora SHALL print the error message and a retry hint without saving a partially tested configuration as valid
5. WHEN the user declines the connection test, THE Aurora SHALL save the configuration without testing

### Requirement 6

**User Story:** AS an existing user, I want to re-run the wizard at any time, so that I can switch providers or models without editing files.

#### Acceptance Criteria

1. WHEN a config file already exists and the user runs `aurora setup`, THE Aurora SHALL prefill each step with the current saved value
2. WHEN the user confirms the wizard, THE Aurora SHALL overwrite the saved values
3. WHEN the user aborts the wizard, THE Aurora SHALL keep the existing configuration unchanged
