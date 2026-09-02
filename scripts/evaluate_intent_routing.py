#!/usr/bin/env python3
"""Evaluate semantic integrated-risk routing with diverse natural-language prompts."""

from __future__ import annotations

import asyncio
import json

from oasis.agent import intent_agent
from oasis.runtime import _intent_model_settings, _select_model
from oasis.settings import Settings


PROMPTS = [
    "What is the flood risk and social-equity priority across Glasgow tomorrow?",
    "Which Glasgow communities should emergency planners prioritise over the next day?",
    "Map hazard, exposed people and vulnerability for Glasgow for the coming 24 hours.",
    "Where will socially vulnerable residents face the greatest flood burden tomorrow?",
    "Rank Glasgow Data Zones for equitable flood intervention in the next 24h.",
    "Show tomorrow's flood exposure and deprivation-aware priority across the city.",
    "Assess who and what is exposed to flooding in Glasgow and recommend priority areas.",
    "Combine surface-water, river and coastal hazard with social vulnerability for tomorrow.",
    "I need a citywide flood risk assessment that accounts for population and SIMD.",
    "Which neighbourhoods have both flood hazard and limited access to emergency services?",
    "Compare Glasgow communities by hazard, exposure, vulnerability and intervention priority.",
    "Give me an equity-weighted 24-hour flood decision map for Glasgow.",
    "Where should limited flood-response resources go if fairness matters most?",
    "Calculate a deprivation-sensitive flood priority ranking for the whole of Glasgow.",
    "Show the next-day risk to people, buildings and critical facilities by Data Zone.",
    "Identify vulnerable Glasgow areas exposed to tomorrow's multi-source flooding.",
    "Run the full social flood-risk analysis, not just a hazard map.",
    "How would a social-equity lens change Glasgow's flood intervention order tomorrow?",
    "格拉斯哥未来24小时哪些社区的洪水风险和社会脆弱性最高？",
    "请结合危险度、人口暴露和SIMD给格拉斯哥社区排序。",
    "明天全市洪水资源应该优先投向哪里，尤其考虑弱势群体？",
    "不要只看hazard，分析Glasgow的exposure、vulnerability和priority。",
    "未来一天格拉斯哥的公平洪水干预地图是什么样？",
    "哪些Data Zone同时存在洪水暴露、老龄人口和交通弱势？",
]


async def main() -> None:
    settings = Settings.from_env()
    model = _select_model(None, settings)
    results = []
    for prompt in PROMPTS:
        output = await intent_agent.run(
            f"COMPACT MAP CONTEXT:\n{{}}\n\nUSER MESSAGE:\n{prompt}",
            model=model,
            model_settings=_intent_model_settings(settings),
        )
        results.append(
            {
                "prompt": prompt,
                "category": output.output.category,
                "pass": output.output.category == "integrated_risk",
            }
        )
    passed = sum(item["pass"] for item in results)
    print(json.dumps({"passed": passed, "total": len(results), "results": results}, ensure_ascii=False, indent=2))
    if passed < 20:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
