import asyncio
import json

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from openai import AsyncOpenAI
from pydantic import BaseModel

client = AsyncOpenAI(
    api_key="sk-XXXXXXXXXXXXXXXXXXXX",
    base_url="https://api.deepseek.com",
)
set_default_openai_client(client)
set_tracing_disabled(True)


# ============================================================
# 1. 定义输出结构（Pydantic 模型）
# ============================================================
class TranslationResult(BaseModel):
    """最终输出的 JSON 结构"""
    query_input: str   # 用户的原始输入
    language: str      # 识别出的语种
    result: str        # 翻译结果


# ============================================================
# 2. 定义两个翻译 Agent（都带 output_type）
# ============================================================
# 非中文 → 翻译为中文
to_chinese_agent = Agent(
    name="ToChineseAgent",
    model="deepseek-flash",
    instructions=(
        "You are a translation agent. The user's input is in a non-Chinese language. "
        "You must:\n"
        "1. Identify the exact language of the input (e.g., 'English', 'Japanese', 'French').\n"
        "2. Translate the input into Chinese.\n"
        "3. Return the result in the required structured format.\n\n"
        "The 'query_input' field must be the exact original input text.\n"
        "The 'language' field must be the detected language name in English.\n"
        "The 'result' field must be the Chinese translation."
    ),
    handoff_description=(
        "Use this agent when the input text is NOT in Chinese. "
        "It will detect the language and translate to Chinese."
    ),
    output_type=TranslationResult,
)

# 中文 → 翻译为英文
to_english_agent = Agent(
    name="ToEnglishAgent",
    model="deepseek-flash",
    instructions=(
        "You are a translation agent. The user's input is in Chinese. "
        "You must:\n"
        "1. Confirm the language is Chinese.\n"
        "2. Translate the input into English.\n"
        "3. Return the result in the required structured format.\n\n"
        "The 'query_input' field must be the exact original input text.\n"
        "The 'language' field must be 'Chinese'.\n"
        "The 'result' field must be the English translation."
    ),
    handoff_description=(
        "Use this agent when the input text IS in Chinese. "
        "It will translate the text to English."
    ),
    output_type=TranslationResult,
)


# ============================================================
# 3. 定义分诊 Agent（Triage Agent）
# ============================================================
triage_agent = Agent(
    name="TriageAgent",
    model="deepseek-flash",
    instructions=(
        "You are a language routing agent. Your ONLY job is to determine whether "
        "the user's input text is in Chinese or not, and then hand off to the "
        "appropriate translation agent.\n\n"
        "Rules:\n"
        "- If the input text is written in Chinese, hand off to the English translator "
        "(ToEnglishAgent).\n"
        "- If the input text is in ANY other language (English, Japanese, French, etc.), "
        "hand off to the Chinese translator (ToChineseAgent).\n\n"
        "Do NOT translate anything yourself. Do NOT produce a final answer. "
        "ONLY perform the handoff. You must call one of the handoff tools."
    ),
    handoffs=[to_chinese_agent, to_english_agent],
)


# ============================================================
# 4. 运行入口
# ============================================================
async def translate(text: str) -> dict:
    """
    对输入文本进行语种判断和翻译，返回指定格式的 JSON。

    Args:
        text: 用户输入的文本

    Returns:
        包含 query_input、language、result 的字典
    """
    result = await Runner.run(triage_agent, input=text)

    # final_output 已经被 SDK 自动解析为 TranslationResult 实例
    output: TranslationResult = result.final_output
    return output.model_dump()


async def main():
    # 如需，可在此设置 API Key：
    # os.environ["OPENAI_API_KEY"] = "sk-..."

    print("=" * 50)
    print("多语种翻译助手（输入 exit / quit / q 退出）")
    print("=" * 50)

    while True:
        # input() 是阻塞调用，用 to_thread 包装，避免阻塞事件循环
        text = await asyncio.to_thread(input, "\n请输入文本: ")

        text = text.strip()
        if not text:
            print("输入为空，请重新输入。")
            continue

        if text.lower() in ("exit", "quit", "q"):
            print("已退出。")
            break

        try:
            result = await translate(text)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"处理出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())