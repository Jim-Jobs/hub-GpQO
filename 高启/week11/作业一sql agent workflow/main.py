"""SQL Agent 启动入口。

安装依赖：python -m pip install sqlalchemy openai
运行方式：python main.py
需配置环境变量 OPENAI_API_KEY 和 OPENAI_BASE_URL。
"""

import os
from pathlib import Path

from openai import OpenAI

from database import create_database_engine
from workflow import run_workflow


os.environ["OPENAI_API_KEY"] = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

def main() -> None:
    """读取配置并启动一次完整问答，结束后释放客户端与连接池。"""
    # 默认寻找 main.py 同目录下的 chinook.db；可通过环境变量覆盖。
    default_path = Path(__file__).resolve().parent / "chinook.db"
    db_path = os.environ.get("SQL_AGENT_DB_PATH", str(default_path))
    # model = os.environ.get("SQL_AGENT_MODEL", "qwen-plus")
    model="deepseek-flash"
    question = "每位艺术家分别有多少张专辑？请显示艺术家名称，按专辑数量降序排列。"

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ["OPENAI_BASE_URL"]
    if not api_key or not base_url:
        raise ValueError("请先配置 OPENAI_API_KEY 和 OPENAI_BASE_URL 环境变量。")

    engine = create_database_engine(db_path)
    try:
        with OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
            max_retries=1,
        ) as client:
            result = run_workflow(question, engine, client, model)
            print("\n最终回答：")
            print(result["answer"])
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
