"""会话自动命名插件：首轮回复结束后，用主模型给「新会话」起名。

- 触发：generate.end 且会话标题仍是默认值且消息里存在真实用户提问；
- 实现：非流式调用主端点，生成 ≤12 字标题后 rename；
- 防抖：同一会话同时只跑一个命名任务；失败静默（不影响对话主流程）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.hooks import HookBus
from ...services.sessions import SessionStore

logger = logging.getLogger("aiassistant.plugins.auto_title")

MAX_TITLE_LEN = 12

TITLE_PROMPT = (
    "你是标题生成器。根据用户的第一句话，输出一个不超过10个字的名词短语标题，"
    "概括对话主题。\n"
    "硬性要求：\n"
    "1. 只输出标题文本，禁止输出任何解释、思考过程、寒暄或前缀（如「标题：」）；\n"
    "2. 用名词短语，不用完整句子（正确示例：北京天气查询；错误示例：用户想查询北京的天气）；\n"
    "3. 不加引号、句号或任何标点结尾；\n"
    "4. 用户问什么就概括什么，不要说「我」「用户」「您」之类的人称。"
)

# 常见口癖/前缀：模型不听话时兜底清洗
_STRIP_PREFIXES = (
    "标题：", "标题:", "好的，", "好的,", "我注意到", "用户想", "用户询问",
    "这是", "关于",
)


def clean_title(raw: str) -> str:
    """清洗模型输出：取首个有效行、去引号与前缀、截断到限长。"""
    line = next(
        (ln.strip() for ln in raw.splitlines() if ln.strip()),
        "",
    )
    line = line.strip().strip('"「」《》\'\'').strip()
    for prefix in _STRIP_PREFIXES:
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
    # 句子式输出（含句号/问号）截到第一个标点
    for sep in ("。", "，", "！", "？", "；", ".", ",", "!", "?", ";"):
        if sep in line:
            line = line.split(sep)[0]
            break
    line = line.strip().strip('"「」《》\'\'').strip()
    return line.replace("\n", "")[:MAX_TITLE_LEN]


class AutoTitlePlugin:
    """把「首条消息截断」的默认标题升级为 LLM 概括标题。

    仅当用户未自定义标题（has_custom_title=False）时触发一次；
    命名后即写入 _titles，不会再对同一会话重复命名。
    """
    def __init__(self, sessions: SessionStore, registry: Any) -> None:
        self._sessions = sessions
        self._registry = registry
        self._inflight: set[str] = set()
        self._background: list[asyncio.Task] = []

    def register(self, hooks: HookBus) -> None:
        hooks.subscribe("generate.end", self._on_generate_end, name="auto_title", priority=200)

    def _should_name(self, session_id: str) -> bool:
        return not self._sessions.has_custom_title(session_id)

    async def _on_generate_end(self, payload: dict[str, Any]) -> None:
        session_id = str(payload.get("session_id") or "")
        if not session_id or session_id in self._inflight:
            return
        if not self._should_name(session_id):
            return
        self._inflight.add(session_id)
        # 后台任务：不阻塞对话收尾；持引用防 GC，结束清除 inflight
        task = asyncio.create_task(self._title_session(session_id))
        self._background.append(task)
        task.add_done_callback(self._background.remove)

    async def _title_session(self, session_id: str) -> None:
        try:
            history = self._sessions.load(session_id)
            first_user = next(
                (
                    str(m.get("content") or "")
                    for m in history
                    if m.get("role") == "user"
                    and not str(m.get("content") or "").startswith("<tool_result")
                    and str(m.get("content") or "").strip()
                ),
                "",
            )
            if not first_user:
                return
            client = self._registry.get("main")
            if client is None:
                return
            resp = await asyncio.wait_for(
                client.chat_completion(
                    [
                        {"role": "system", "content": TITLE_PROMPT},
                        {"role": "user", "content": first_user[:200]},
                    ],
                    max_tokens=48,
                    temperature=0.2,
                ),
                timeout=20,
            )
            raw = str(
                resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            title = clean_title(raw)
            if title:
                self._sessions.rename(session_id, title)
                logger.info("自动命名: %s -> %s", session_id, title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动命名失败(已忽略): %s: %s", session_id, exc)
        finally:
            self._inflight.discard(session_id)
