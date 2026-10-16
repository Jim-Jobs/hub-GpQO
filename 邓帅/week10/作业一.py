from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")
set_tracing_disabled(True)

from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model = os.getenv("BASE_MODEL") or "qwen3.8-max"

from pydantic import BaseModel

class TranslateResult(BaseModel):
    """用户输入"""
    query_input: str
    """用户输入的语言"""
    language: str
    """翻译结果"""
    result: str

from openai import AsyncOpenAI
from agents import Agent, OpenAIChatCompletionsModel, Runner

openai_client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url
)

translate_agent_prompt = """
你是文本翻译助手。如果用户的输入为中文，你需要将用户输入翻译成英文。如果用户输出为非中文，你需要将用户输出翻译为中文。
"""


translate_agent = Agent(
    name="TranslateAssistant",
    instructions=translate_agent_prompt,
    model=OpenAIChatCompletionsModel(
        model=model,
        openai_client=openai_client,
    ),
    output_type=TranslateResult
)

async def main():
    user_input = input("请输入要翻译的文本：")
    result = await Runner.run(translate_agent, input=user_input)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
