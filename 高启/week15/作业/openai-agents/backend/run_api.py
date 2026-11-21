"""以本机 `.env` 中的端口启动 OpenAI Agents API。"""

import os

import uvicorn

from backend.config import load_environment


def main() -> None:
    """加载项目配置并启动 API 服务。

    Args:
        无；API 端口从项目根目录 `.env` 的 `API_PORT` 读取。

    Returns:
        服务停止后无返回值。

    Raises:
        ValueError: API_PORT 不是有效整数。
    """
    load_environment()
    uvicorn.run("backend.api:app", host="0.0.0.0", port=int(os.getenv("API_PORT", "8001")))


if __name__ == "__main__":
    main()
