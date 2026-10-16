import asyncio
import json
import os
from enum import Enum

from pydantic import BaseModel

from agents import Agent, Runner
from agents import (
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled,
    OpenAIChatCompletionsModel,
)
from openai import AsyncOpenAI

DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxx"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"  

set_tracing_disabled(True)

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

set_default_openai_client(client)
set_default_openai_api("chat_completions")

class Language(str, Enum):
    CHINESE = "中文"
    ENGLISH = "英语"
    SPANISH = "西班牙语"
    FRENCH = "法语"
    GERMAN = "德语"
    JAPANESE = "日语"
    KOREAN = "韩语"
    RUSSIAN = "俄语"
    OTHER = "其他"


class TranslationOutput(BaseModel):
    query_input: str
    language: str
    result: str

model = OpenAIChatCompletionsModel(
    model=DEEPSEEK_MODEL,
    openai_client=client,
)

agent = Agent(
    name="语言识别与翻译助手",
    model=model, 
    instructions="""你是一个语言识别和翻译助手。

任务要求：
1. 首先识别用户输入文本的语种。
2. 判断规则：
   - 如果文本是中文，则将其中文翻译为英文，并将翻译结果放入 result 字段。
   - 如果文本是非中文（如英语、日语、法语等），则将其翻译为中文，并将翻译结果放入 result 字段。
3. 语言字段 language 应填写识别出的语种名称（如"中文"、"英语"、"日语"等）。
4. query_input 字段应原样保留用户输入的文本。
5. 严格按照指定的 JSON 格式输出，不要添加任何额外说明、解释、markdown 代码块或标记：
    {"query_input": "原文", "language": "语种", "result": "译文"}""",
)


async def translate_text(text: str) -> dict:
    """执行语言识别和翻译，返回符合要求的字典。"""
    result = await Runner.run(agent, input=text)
    raw = str(result.final_output).strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)

async def main():
    user_input = input("请输入文本: ").strip()
    if not user_input:
        print("输入不能为空")
        return

    output_dict = await translate_text(user_input)

    print(json.dumps(output_dict, ensure_ascii=False, indent=4))

if __name__ == "__main__":
    asyncio.run(main())