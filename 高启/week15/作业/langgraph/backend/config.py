"""加载项目本机运行使用的环境配置。"""

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """读取项目根目录 `.env`，保留调用进程已设置的环境变量。

    Args:
        无；配置文件固定为项目根目录下的 `.env`。

    Returns:
        配置加载完成后无返回值。

    Raises:
        无；配置文件缺失时沿用进程当前环境变量。
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
