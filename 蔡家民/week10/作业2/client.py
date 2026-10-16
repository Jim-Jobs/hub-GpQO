"""连接 MCP 服务，让 Agent 调用工具；运行结束后打印调用记录。"""
import asyncio
import json

from agents import Agent, Runner
from agents.mcp import MCPServerSse

from config import create_model


async def main():
    client, model = create_model()
    async with client:
        # SDK 提供的连接对象，连接 server.py 启动的 MCP 服务。
        async with MCPServerSse(
            name="Homework2 MCP Server",
            params={"url": "http://127.0.0.1:8000/sse"},
            client_session_timeout_seconds=180,
        ) as mcp_server:
            tools = await mcp_server.list_tools()
            print("可用工具：", ", ".join(tool.name for tool in tools))

            agent = Agent(
                name="Assistant",
                model=model,
                mcp_servers=[mcp_server],
                instructions=(
                    "你是工具助手。翻译或语种识别必须调用 translate_text_tool；"
                    "温度转换必须调用 convert_temperature；日期间隔必须调用 calculate_date_difference。"
                    "根据工具返回结果回答，不自行替代工具计算或翻译。缺少必要参数时询问用户。"
                    "工具失败时如实说明，不编造结果。get_news 仅为模拟新闻，回答时明确说明。"
                    "通常用中文回答，用户要求翻译时保留工具给出的译文。"
                ),
            )

            print("每个问题独立处理，输入 exit 或 退出 结束。")
            while True:
                question = input("\n请输入问题：").strip()
                if question.lower() in {"exit", "quit", "退出"}:
                    break
                if not question:
                    continue

                # 整次运行结束才返回 result；这里不是流式输出。
                result = await Runner.run(agent, question, max_turns=10)
                for item in result.new_items:
                    if item.type == "tool_call_item":
                        print(f"工具调用：{item.raw_item.name}；参数：{item.raw_item.arguments}")
                    elif item.type == "tool_call_output_item":
                        print("工具返回：", json.dumps(item.output, ensure_ascii=False, default=str))
                print("最终回答：", result.final_output)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
