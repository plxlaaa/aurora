#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_fetch skill 执行入口。

参数通过环境变量 SKILL_ARGUMENTS 以 JSON 字符串传入（注入约定见 backend/app/skills.py：
env["SKILL_ARGUMENTS"] = json.dumps(arguments)）。
支持的参数：
  url      必填，抓取目标网页的完整 URL（string）
  max_chars 可选，限制返回正文的最大字符数（integer，默认 4000）
本脚本不依赖第三方库，可直接用 python3 独立运行，向 stdout 打印一条 JSON 结果；
网络错误或页面解析异常时返回 {"url": ..., "error": ...}。

独立运行示例：
    SKILL_ARGUMENTS='{"url":"https://example.com","max_chars":2000}' python3 run.py
"""
import json
import os
import urllib.request
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    """提取页面正文文本，跳过 script/style 标签内容。"""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def text(self, limit=4000):
        out = " ".join(" ".join(self._text).split())
        return out[:limit]


def fetch_page(url: str, max_chars: int = 4000) -> dict:
    """抓取 URL 并提取标题与正文；失败时返回 error。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ai-assistant/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = TextExtractor()
        parser.feed(html)
        title = ""
        low = html.lower()
        start = low.find("<title") + 7
        if start > 7:
            end = html.find(">", start)
            title = html[end + 1: html.find("</title", end)].strip()
        return {"url": url, "title": title[:200], "text": parser.text(max_chars)}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": str(exc)}


def main() -> None:
    # 从环境变量读取参数（skills.py 注入）
    raw = os.environ.get("SKILL_ARGUMENTS", "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    url = payload.get("url")
    if not url:
        print(json.dumps({"error": "缺少参数 url"}, ensure_ascii=False))
        return
    try:
        max_chars = int(payload.get("max_chars", 4000))
    except (TypeError, ValueError):
        max_chars = 4000
    print(json.dumps(fetch_page(url, max_chars), ensure_ascii=False))


if __name__ == "__main__":
    main()