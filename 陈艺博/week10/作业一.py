import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = "OPENAI_API_KEY"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

from pydantic import BaseModel
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel,function_tool

external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )


# 1. 定义期望的JSON输出结构
class TranslationOutput(BaseModel):
    query_input: str
    language: str
    result: str


# 2. 定义工具函数
@function_tool
def detect_language(text: str) -> str:
    """检测输入文本的语种"""
    # 这里可以接入真实的语言检测库，比如 langdetect，或者简单模拟
    # 例如：检测到中文返回 'zh'，英文返回 'en'
    # 实际调用时，Agent 的 LLM 通常也能直接判断，但按题目要求封装为工具
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return "zh"
    return "en"


@function_tool
async def translate_text(text: str, target_lang: str) -> str | None:
    """将文本真正地翻译为目标语言。"""
    response = await external_client.chat.completions.create(
        model="qwen3.8-27b",
        messages=[
            {"role": "system", "content": f"请将以下文本翻译成{target_lang}。直接输出翻译结果，不要任何解释和引号。"},
            {"role": "user", "content": text}
        ],
        temperature=0.3 # 翻译任务降低随机性
    )
    return response.choices[0].message.content


# 3. 创建 Agent
# 设定系统提示词，明确逻辑：非中文 -> 翻译为中文；中文 -> 翻译为英文
agent = Agent(
    name="Translation Assistant",
    instructions="""你是一个语言助手。用户会输入一段文本。
    1. 首先判断文本的语种。
    2. 如果语种不是中文，将其翻译成中文。
    3. 如果语种是中文，将其翻译成英文。
    4. 严格按照以下 JSON 格式输出：{"query_input": "", "language": "", "result": ""}""",
    tools=[detect_language, translate_text],
    model=OpenAIChatCompletionsModel(
        model="qwen3.8-27b",
        openai_client=external_client,
    ),
    output_type=TranslationOutput
)







# 4. 运行示例
async def main():

    user_input_1 = "Hello, how are you?"
    result = await Runner.run(agent, user_input_1)

    # 最终输出严格符合要求的JSON
    print(result.final_output.model_dump_json(indent=4))

    user_input_2 = "欢迎学习英语"
    result = await Runner.run(agent, user_input_2)

    # 最终输出严格符合要求的JSON
    print(result.final_output.model_dump_json(indent=4))

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())