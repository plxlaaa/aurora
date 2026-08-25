#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather skill 执行入口。

参数通过环境变量 SKILL_ARGUMENTS 以 JSON 字符串传入（注入约定见 backend/app/skills.py：
env["SKILL_ARGUMENTS"] = json.dumps(arguments)）。
本脚本不依赖第三方库，可直接用 python3 独立运行，向 stdout 输出一条 JSON 结果，
键含 city 与 weather；网络不可用或接口异常时返回合理的降级结果。

独立运行示例：
    SKILL_ARGUMENTS='{"city":"北京"}' python3 run.py
"""
import json
import os
import urllib.parse
import urllib.request


def get_weather(city: str) -> dict:
    """尝试通过 wttr.in 开放接口查询实时天气；失败时返回合理的降级结果。"""
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cur = data.get("current_condition", [{}])[0]
        desc = (
            cur.get("lang_zh", [{}])[0].get("value")
            if cur.get("lang_zh")
            else cur.get("weatherDesc", [{}])[0].get("value")
        )
        return {
            "city": city,
            "weather": desc or "未知",
            "temperature": cur.get("temp_C"),
            "humidity": cur.get("humidity"),
            "wind_speed": cur.get("windspeedKmph"),
        }
    except Exception as exc:  # noqa: BLE001
        # 网络/接口不可用时的合理降级结果
        return {
            "city": city,
            "weather": "查询失败（网络不可用或服务异常）",
            "detail": str(exc),
        }


def main() -> None:
    # 从环境变量读取参数（skills.py 注入）
    raw = os.environ.get("SKILL_ARGUMENTS", "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    city = payload.get("city")
    if not city:
        print(json.dumps({"error": "缺少参数 city"}, ensure_ascii=False))
        return
    print(json.dumps(get_weather(city), ensure_ascii=False))


if __name__ == "__main__":
    main()