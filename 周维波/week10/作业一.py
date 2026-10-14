import os

os.environ["OPENAI_API_KEY"] = "sk-abropaugscvtwiktemavqawlgvjgsmqpfahasxxpajfpvhld"
os.environ["OPENAI_BASE_URL"] = "https://api.siliconflow.cn/v1"

import asyncio
import json
from pydantic import BaseModel
from openai import AsyncOpenAI
from agents import Agent, InputGuardrail, GuardrailFunctionOutput, Runner, OpenAIChatCompletionsModel
from agents.exceptions import InputGuardrailTripwireTriggered
# from agents.extensions.visualization import draw_graph

# 大模型调用协议
# 硅基流动兼容 OpenAI Chat Completions，不支持 Responses
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")  # 强制 SDK 使用 Chat Completions API，而非默认的 Responses API
set_tracing_disabled(True)  # 彻底关闭 SDK 的链路追踪（Tracing）功能



LLM = OpenAIChatCompletionsModel(
    model="Pro/deepseek-ai/DeepSeek-V3.2",
    openai_client=AsyncOpenAI(),
)


# 最终输出结构：与需求 JSON 完全一致 {query_input, language, result}
class TranslationOutput(BaseModel):
    """用于承载最终翻译结果的结构"""
    query_input: str  # 用户输入的原始文本（原文，逐字保留）
    language: str     # 输入文本所属语种的中文名称，如：中文、英文、日文
    result: str       # 翻译后的结果文本


# 守卫检查用结构
class InputValidity(BaseModel):
    """用于判断用户输入是否为有意义的文本（真实语言内容）"""
    is_valid: bool


# 守卫检查代理 -》 本质也是通过大模型调用完成的
guardrail_agent = Agent(
    name="Input Check Agent",
    model=LLM,
    instructions="判断用户输入是否为有意义的文本（包含真实的语言文字内容，非空、非纯符号、非纯数字）。如果是，'is_valid' 应为 True，json 返回",
    output_type=InputValidity,  # openai 官方推荐的语法，推荐大模型输出的格式类型，国内模型支持的不太好；
)


# 翻译成中文代理
translate_to_chinese_agent = Agent(
    name="Translate To Chinese",
    model=LLM,
    handoff_description="负责将非中文文本翻译成简体中文的专家代理。",
    instructions="您是专业翻译。将用户输入的原始文本翻译为简体中文，并严格按照 TranslationOutput 结构输出 json：query_input 为原文逐字保留，language 为输入文本语种的中文名称（如 中文、英文、日文、法文），result 为简体中文翻译结果。",
    output_type=TranslationOutput,
)

# 翻译成英文代理
translate_to_english_agent = Agent(
    name="Translate To English",
    model=LLM,
    handoff_description="负责将中文文本翻译成英文的专家代理。",
    instructions="您是专业翻译。将用户输入的原始文本翻译为英文，并严格按照 TranslationOutput 结构输出 json：query_input 为原文逐字保留，language 为输入文本语种的中文名称（如 中文、英文、日文、法文），result 为英文翻译结果。",
    output_type=TranslationOutput,
)


async def input_validity_guardrail(ctx, agent, input_data):
    """
    运行检查代理来判断输入是否为有意义的文本。
    如果不是有意义的文本（'is_valid' 为 False），则触发阻断 (tripwire)。
    """
    print(f"\n[Guardrail Check] 正在检查输入: '{input_data}'...")

    # 运行检查代理
    result = await Runner.run(guardrail_agent, input_data, context=ctx.context)

    # 解析输出
    final_output = result.final_output_as(InputValidity)

    tripwire_triggered = not final_output.is_valid

    return GuardrailFunctionOutput(
        output_info=final_output,
        tripwire_triggered=tripwire_triggered,
    )


# 先进行输入的校验 guardrail_agent
# triage_agent 判断 translate_to_chinese_agent / translate_to_english_agent
# 对应专家代理完成翻译
triage_agent = Agent(
    name="Triage Agent",
    model=LLM,
    instructions="您的任务是根据用户输入文本的语种，判断应该将请求分派给 'Translate To Chinese'（非中文文本）还是 'Translate To English'（中文文本）。混合语种按占比最高者判定，无法判断时默认派发给 'Translate To Chinese'。交接时务必附上用户输入的原始文本原文，不要改写。",
    handoffs=[translate_to_chinese_agent, translate_to_english_agent],
    input_guardrails=[
        InputGuardrail(guardrail_function=input_validity_guardrail),
    ],
)


def parse_output(result) -> TranslationOutput:
    """将 Runner 的最终输出解析为 TranslationOutput（兼容 str 与结构化对象两种情况）"""
    final = result.final_output
    if isinstance(final, TranslationOutput):
        return final
    if isinstance(final, str):
        return TranslationOutput.model_validate_json(final)
    raise TypeError(f"无法解析最终输出，类型为: {type(final)}")


async def run_query(query: str):
    """运行一次：守卫校验 -》 triage 路由 -》 对应翻译代理"""
    print(f"**用户输入:** {query}")
    result = await Runner.run(triage_agent, query)
    output = parse_output(result)
    print("\n**✅ 流程通过，最终输出:**")
    print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))


async def main():
    print("--- 启动语种识别 + 翻译代理系统示例 ---")

    print("\n" + "=" * 50)
    print("=" * 50)
    try:
        query = "Hello, world! How are you today?"
        await run_query(query)  # 英文 -> 翻译为中文
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)

    print("\n" + "=" * 50)
    print("=" * 50)
    try:
        query = "今天天气真不错，我们出去走走吧。"
        await run_query(query)  # 中文 -> 翻译为英文
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)

    print("\n" + "=" * 50)
    print("=" * 50)
    try:
        query = "Bonjour le monde"
        await run_query(query)  # 法文 -> 翻译为中文
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)

    print("\n" + "=" * 50)
    print("=" * 50)
    try:
        query = "12345"  # 纯数字，无实际语言内容，应被守卫阻断
        await run_query(query)
        print("\n**✅ 流程通过，最终输出:**")  # 这行应该不会被执行
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:** 输入被阻断，因为它不是有意义的文本。")
        print(e)


if __name__ == "__main__":
    asyncio.run(main())

    # try:
    #     draw_graph(triage_agent, filename="03_基础使用")
    # except:
    #     print("绘制agent失败，默认跳过。。。")

# python 03_语种识别翻译_demo.py
