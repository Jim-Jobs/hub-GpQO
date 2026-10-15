from openai import AsyncOpenAI
from pydantic import BaseModel
from agents import Agent, Runner, set_default_openai_client, set_default_openai_api, set_tracing_disabled

# ========== 配置自定义模型服务商 ==========
client = AsyncOpenAI(
    base_url="https://api.deepseek.com/v1",  # DeepSeek 的 API 地址
    api_key="***",  # 替换为你的实际 key
)
set_default_openai_client(client, use_for_tracing=False)
set_default_openai_api("chat_completions")  # DeepSeek 使用 Chat Completions 协议
set_tracing_disabled(True)  # 关闭 tracing，消除提示日志
# ==========================================

PROMPT = (
    "你是翻译助手小凡。\n"
    "你需要根据用户输入(query_input)判断语种类型(language)是否为中文:0 非中文,1 中文。\n"
    "如果不是中文,请翻译成中文;如果是中文,请翻译成英文,保存翻译结果(result)。\n"
    "请严格只输出以下JSON,不要附加其他文字:\n"
    '{"query_input": "...", "language": 0或1, "result": "..."}'
)


class ResultData(BaseModel):
    query_input: str
    """用户输入"""
    language: int
    """语种类型 0 非中文 1 中文"""
    result: str
    """翻译结果"""


class TextDetectionTranslationAgent:
    tdt_agent = Agent(
        name="TdtAgent",
        instructions=PROMPT,
        model="deepseek-chat",
    )


if __name__ == '__main__':
    result = Runner.run_sync(TextDetectionTranslationAgent.tdt_agent, "机器学习")
    # DeepSeek 不支持 json_schema response_format，手动解析返回的 JSON 文本
    data = ResultData.model_validate_json(result.final_output)
    print(data.model_dump_json(indent=2))
