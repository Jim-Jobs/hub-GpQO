"""
作业1：语种判断与翻译

用户输入一段文本：
1. 判断这段文本是什么语言
2. 非中文翻译成中文；中文翻译成英文
3. 按如下 JSON 输出：
   {
       "query_input": "",
       "language": "",
       "result": ""
   }
"""

import os

# 与 0-环境配置/00_openai-agent环境.py 保持一致
os.environ["OPENAI_API_KEY"] = "sk-bf4240833ba44d6ea4e6b33dacd6be11"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio
import json
import sys

from pydantic import BaseModel

from agents import Agent, ModelSettings, Runner
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


class LanguageOutput(BaseModel):
    """语种识别结果。"""

    language: str
    """语种的中文名称，例如：中文、英文、日文。"""

    is_chinese: bool
    """主要语言是否为中文（含简体、繁体）。"""


# 第一步：只判断语种，不翻译。
# 当前 DeepSeek 不支持 Agents 默认的 json_schema，这里用 json_object，再在代码里解析。
language_agent = Agent(
    name="language_detector",
    model="deepseek-chat",
    instructions=(
        "判断用户输入文本的主要语种，只输出一个 JSON 对象，不要翻译、不要解释。\n"
        "格式：{\"language\": \"语种中文名\", \"is_chinese\": true或false}\n"
        "language 例如：中文、英文、日文、韩文、法文。\n"
        "只有主要语言是中文（简体或繁体）时，is_chinese 才为 true。"
    ),
    model_settings=ModelSettings(
        extra_args={"response_format": {"type": "json_object"}},
    ),
)

# 第二步：按语种选择其中一个翻译 Agent
to_chinese_agent = Agent(
    name="to_chinese_translator",
    model="deepseek-chat",
    instructions="把用户给出的文本准确翻译成简体中文。只输出译文，不要解释。",
)

to_english_agent = Agent(
    name="to_english_translator",
    model="deepseek-chat",
    instructions="把用户给出的文本准确翻译成英文。只输出译文，不要解释。",
)


def _as_language(output) -> LanguageOutput:
    if isinstance(output, LanguageOutput):
        return output
    if isinstance(output, dict):
        return LanguageOutput.model_validate(output)
    raw = str(output).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"语种识别结果不是 JSON：{raw}")
    return LanguageOutput.model_validate(json.loads(raw[start : end + 1]))


def _is_chinese(detected: LanguageOutput) -> bool:
    name = detected.language.strip().lower()
    chinese_names = {"中文", "汉语", "汉文", "简体中文", "繁体中文", "chinese", "zh", "zh-cn", "zh-tw"}
    if name in chinese_names:
        return True
    return detected.is_chinese


async def translate_text(text: str) -> dict:
    # 串行编排：先识别语种，再按规则交给对应翻译 Agent
    detected = _as_language((await Runner.run(language_agent, text)).final_output)

    translator = to_english_agent if _is_chinese(detected) else to_chinese_agent
    translated = await Runner.run(translator, text)

    return {
        "query_input": text,
        "language": detected.language,
        "result": str(translated.final_output).strip(),
    }


def _read_user_text() -> str:
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()
    return input("请输入要判断并翻译的文本：").strip()


async def main():
    text = _read_user_text()
    if not text:
        print("输入不能为空。")
        return

    result = await translate_text(text)
    print(json.dumps(result, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())
