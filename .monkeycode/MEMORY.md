# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[Project Knowledge Summary]
- Date: 2026-08-25
- Context: Discovered by Agent while optimizing backend code of the AI Assistant project
- Category: Environment Configuration
- Instructions:
  - This dev sandbox lacks the python3-venv / python3.11-venv apt package, so `python3 -m venv` fails with ensurepip errors. Install backend dependencies directly with `python3 -m pip install --break-system-packages -r backend/requirements.txt`.
  - Run backend unit tests with `cd backend && python3 -m unittest discover -s tests -v` (93 tests as of 2026-08-28).

[Project Knowledge Summary]
- Date: 2026-08-28
- Context: Discovered by Agent while verifying the AI tool system end-to-end
- Category: Operations & Deployment
- Instructions:
  - Backend restart command: `cd /workspace/backend && export AI_ASSISTANT_CONFIG_DIR=/workspace/config AI_ASSISTANT_DATA_DIR=/workspace/data AI_ASSISTANT_SKILLS_DIR=/workspace/config/skills AI_ASSISTANT_UPLOAD_DIR=/workspace/data/uploads AI_ASSISTANT_SANDBOX_LOCAL_DIR=/workspace/sandbox && python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8080` (run as a managed background terminal).
  - `config/models.yaml` and `config/sandbox.yaml` are local-only env configs (models.yaml holds the real SiliconFlow API key); never commit them.
  - Docker is unavailable in this sandbox: keep `sandbox.yaml` mode=local with allow=true, workdir=/workspace/sandbox; changing sandbox.yaml requires a backend restart to take effect.
  - Main model `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` has no function-calling support and often omits the `<tool_call>` markers; the chat pipeline falls back to bare-JSON tool-call parsing (name must exist in the registry).
  - Frontend preview: vite dev server on 5173 (HMR picks up changes automatically); `npm run build` in /workspace/frontend is the build gate.
