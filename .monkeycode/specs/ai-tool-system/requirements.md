# Requirements Document — AI 工具系统重设计

Feature Name: ai-tool-system
Updated: 2026-08-28

## Introduction

重设计 AI 助手的工具体系：AI 在每轮对话中明确知道自身可用的完整工具目录（内置工具、Skills、MCP 工具、用户自定义工具），并按需读取 Skills 详情后自行调用；用户可在设置页管理自定义工具、上传整个 Skill 文件夹、增删改 MCP 服务器。

## Glossary

- **工具（Tool）**: 模型可通过调用协议请求执行的能力单元，含名称、描述、参数 Schema、执行后端。
- **工具目录（Tool Directory）**: 注入对话 system prompt 的清单，列出所有可用工具的名称、来源与一句话描述。
- **内置工具（Built-in Tool）**: 网关自带的工具，如 `read_skill`、`run_skill`、`run_code`。
- **Skill**: `config/skills/<name>/` 下的一个文件夹，含 `manifest.yaml`（元数据）与说明文档及脚本，由 AI 按需读取后再执行。
- **按需读取（On-demand Loading）**: 工具目录仅含 Skill 名称与描述；模型调用 `read_skill` 后才获取该 Skill 的完整使用说明。
- **自定义工具（Custom Tool）**: 用户在设置页定义的工具，执行方式为 HTTP 请求模板或 Python 代码片段。
- **MCP 服务器**: 通过 Model Context Protocol 接入的外部工具集合（stdio/http 传输）。
- **Function Calling 模式**: 模型原生支持 OpenAI tools 协议时的调用方式。
- **文本协议模式（ReAct 模式）**: 模型不支持 function calling 时，以约定文本标记输出工具调用的回退协议。

## Requirements

### Requirement 1 — 工具目录注入

**User Story:** AS AI 模型, I want 在对话中看到完整的工具目录, so that 我明确知道自己拥有哪些工具及其用途。

#### Acceptance Criteria

1. WHEN 每轮对话请求组装 messages 时，系统 SHALL 在 system prompt 中注入工具目录，含每个工具的名称、来源（内置/skill/mcp/自定义）与一句话描述。
2. WHEN 工具集合发生变化（skill 增删、MCP 重连、自定义工具修改），系统 SHALL 在下一次对话请求中使用更新后的工具目录。
3. IF 某类工具为空（如未配置任何 MCP 服务器），系统 SHALL 在工具目录中省略该分组且不报错。

### Requirement 2 — 按需读取 Skills

**User Story:** AS AI 模型, I want 先看到 Skill 摘要再按需读取完整说明, so that 上下文占用最小且我能正确使用 Skill。

#### Acceptance Criteria

1. 系统 SHALL 提供内置工具 `read_skill(name)`，返回该 Skill 的完整说明（manifest 描述、参数说明、使用文档）。
2. WHEN 工具目录组装时，系统 SHALL 仅将 Skill 的名称与描述列入目录，全量说明仅在 `read_skill` 调用后返回。
3. 系统 SHALL 提供内置工具 `run_skill(name, arguments)`，按 manifest 定义执行 Skill 入口并返回结果。
4. IF `read_skill` 收到不存在的 Skill 名称，系统 SHALL 返回包含可用 Skill 名称列表的错误信息。

### Requirement 3 — 双模式工具调用协议

**User Story:** AS 用户, I want 无论主模型是否支持 function calling 都能使用工具, so that 任意模型均可完成工具调用。

#### Acceptance Criteria

1. WHEN 模型支持 function calling，系统 SHALL 使用 OpenAI tools 协议传递工具定义与调用结果。
2. WHEN 网关检测到端点模型不支持 function calling，系统 SHALL 自动切换文本协议模式：system prompt 附带工具调用格式说明，模型以约定标记输出调用，网关解析、执行并回填结果。
3. WHEN 文本协议模式下模型输出的调用标记可被解析，系统 SHALL 执行对应工具并将结果以约定格式回填到对话中继续推理。
4. IF 文本协议模式下模型输出无法解析出工具调用，系统 SHALL 将该轮输出作为普通回复返回给用户。

### Requirement 4 — 自定义工具管理

**User Story:** AS 用户, I want 在设置页创建和管理自定义工具, so that AI 能调用我定义的外部能力。

#### Acceptance Criteria

1. 系统 SHALL 提供自定义工具的创建、编辑、删除、启停接口，字段含名称、描述、参数 Schema、执行方式（http / python）。
2. WHEN 执行方式为 http，自定义工具 SHALL 支持配置 URL、方法、请求头模板与参数到请求的映射。
3. WHEN 执行方式为 python，自定义工具 SHALL 以受限沙箱执行用户提供的代码片段并返回结果。
4. WHEN 自定义工具被保存，系统 SHALL 立即生效并出现在下一轮对话的工具目录中。
5. IF 自定义工具执行失败，系统 SHALL 将错误信息作为工具结果返回给模型继续推理。

### Requirement 5 — Skill 文件夹管理

**User Story:** AS 用户, I want 上传整个 Skill 文件夹并在线管理, so that 无需登服务器即可扩充 AI 能力。

#### Acceptance Criteria

1. 系统 SHALL 提供 Skill 打包上传接口，接受 zip 压缩包并解压至 `config/skills/<name>/`。
2. WHEN 上传的 zip 内存在 manifest.yaml 或 manifest.yml，系统 SHALL 校验其 name 字段与目标目录名一致性后完成注册。
3. IF 上传的 zip 缺少有效 manifest，系统 SHALL 返回明确错误且不写入任何文件。
4. 系统 SHALL 提供 Skill 列表查看（含文件结构）、manifest 在线编辑保存、删除接口。
5. WHEN Skill 文件发生增删改，系统 SHALL 热刷新注册表并在下一轮对话生效。

### Requirement 6 — MCP 服务器管理

**User Story:** AS 用户, I want 在设置页增删改 MCP 服务器配置, so that 动态接入外部工具集。

#### Acceptance Criteria

1. 系统 SHALL 提供 MCP 服务器的创建、编辑、删除、启停接口，字段含名称、描述、传输方式（stdio/http）、命令或 URL、环境变量、API Key。
2. WHEN MCP 配置被保存或删除，系统 SHALL 重连或断开对应 server 并刷新工具列表。
3. 系统 SHALL 在设置页展示每个 server 的连接状态与工具清单。

### Requirement 7 — 工具中心设置页

**User Story:** AS 用户, I want 在设置页集中查看和管理所有工具, so that 清楚 AI 当前拥有哪些能力。

#### Acceptance Criteria

1. 设置页 SHALL 提供「工具中心」分区，按来源（内置/Skills/MCP/自定义）分组列出全部工具，含名称、描述、启停状态。
2. 工具中心 SHALL 提供 Skills 管理（上传/编辑/删除/启停）、MCP 管理（增删改/状态）、自定义工具管理（增删改/测试运行）三个子视图。
3. WHEN 用户执行任一管理操作，界面 SHALL 即时反馈结果并刷新工具列表。

## Non-Goals

- 工具市场/在线安装第三方 Skill 仓库。
- 多用户隔离（延续现有单用户模型）。
- MCP resources/prompts 协议能力（仅 tools）。
