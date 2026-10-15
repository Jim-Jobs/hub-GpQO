from agents import Agent, Runner
import os
import asyncio

os.environ["OPENAI_API_KEY"] = os.environ.get("MINIMAX_API_KEY")
os.environ["OPENAI_BASE_URL"] = "https://api.minimaxi.com/v1"

prompt_tr = """
判断用户文本的语种（是什么语言的）
然后如果是非中文的，则翻译为中文；如果是中文，则翻译为英文。
最终输出格式:
{
    "query_input": "",
    "language": "",
    "result": ""
}
"""

translation_agent = Agent(
    name="Translation Agent",
    model="MiniMax-M3",
    instructions=prompt_tr,
)

async def main():
    query1 = "今天天气真好"
    query2 = "what's your name?"
    result1 = await Runner.run(translation_agent, query1)
    result2 = await Runner.run(translation_agent, query2)
    print(result1.final_output)
    print(result2.final_output)
if __name__ == "__main__":
    asyncio.run(main())
