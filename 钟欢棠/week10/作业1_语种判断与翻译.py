# -*- coding: utf-8 -*-
"""
作业1：语种判断 + 翻译

需求：
    用户输入一段文本，判断文本的语种（是什么语言）。
    - 如果是非中文 -> 翻译成中文
    - 如果是中文   -> 翻译成英文
    最终输出 JSON：
    {
        "query_input": "",   # 用户原始输入
        "language":    "",   # 检测到的语种
        "result":      ""    # 翻译结果
    }

实现思路（基于 openai-agents 框架 + DeepSeek）：
    1. 用 pydantic 定义输出结构 TranslationResult；
    2. DeepSeek 不支持 output_type（结构化输出），所以让 Agent 直接"只输出 JSON 文本"；
    3. 拿到 final_output 后，用 parse_json 解析成 dict，再构造成 TranslationResult。
"""

import os
import json
import asyncio
import re

from pydantic import BaseModel, Field

# ---------------- 环境配置 ----------------
# DeepSeek（deepseek-chat），兼容 OpenAI 协议
os.environ["OPENAI_API_KEY"] = "sk-7c1606376d4c4ac1ae86d1933e6bbca9"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

from agents import Agent, Runner
from agents import set_default_openai_api, set_tracing_disabled

# 强制 SDK 走 Chat Completions 协议
set_default_openai_api("chat_completions")
# 关闭链路追踪
set_tracing_disabled(True)


# ---------------- 输出结构 ----------------
class TranslationResult(BaseModel):
    """语种判断与翻译任务的输出结构"""
    query_input: str = Field(description="用户输入的原始文本")
    language: str = Field(description="检测到的语种，例如：中文/英文/日文等")
    result: str = Field(description="翻译结果：中文->英文，非中文->中文")


# ---------------- Agent 定义 ----------------
translation_agent = Agent(
    name="Language Detection and Translation Agent",
    model="deepseek-chat",
    instructions=(
        "你是一个语种识别与翻译助手，请严格按以下步骤处理用户输入：\n"
        "1. 判断用户输入文本的语种（是什么语言）。\n"
        "2. 如果文本不是中文，则将其翻译成中文。\n"
        "3. 如果文本是中文，则将其翻译成英文。\n"
        "4. 按以下 JSON 结构输出：\n"
        '   {"query_input": "用户原始输入", "language": "检测到的语种", "result": "翻译结果"}\n'
        "只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块标记。"
    ),
)


def parse_json(text: str) -> dict:
    """把模型输出的文本解析成 JSON dict（兼容偶尔出现的 markdown 代码块）"""
    text = text.strip()
    # 去掉可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 截取第一个 { 到最后一个 }，避免模型多输出无关文字
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


async def translate(text: str) -> TranslationResult:
    """对输入文本做语种判断 + 翻译，返回结构化结果"""
    result = await Runner.run(translation_agent, text)
    return TranslationResult(**parse_json(result.final_output))


def to_json(t: TranslationResult) -> str:
    """把结果转成作业要求的 JSON 字符串"""
    return json.dumps(t.model_dump(), ensure_ascii=False, indent=2)


async def main():
    print("=" * 60)
    print("作业1：语种判断与翻译")
    print("=" * 60)

    # 测试用例：中文 -> 英文；英文 -> 中文；日文 -> 中文
    examples = [
        "今天天气真好，我们一起去公园散步吧。",
        "Artificial intelligence is transforming every industry.",
        "今日はとてもいい天気ですね。",
    ]

    for text in examples:
        print(f"\n【输入】{text}")
        output = await translate(text)
        print("【输出JSON】")
        print(to_json(output))


if __name__ == "__main__":
    asyncio.run(main())

# 运行：python 作业1_语种判断与翻译.py
