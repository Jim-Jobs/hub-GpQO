"""应用配置:pydantic-settings 读目录链上的 .env,workflow 默认参数集中可调。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 作业一根目录(backend/ 的父目录),截图等产物落于此
HW11_DIR = Path(__file__).resolve().parents[2]
# backend 目录,chinook.db 与 history.db 落于此
BACKEND_DIR = Path(__file__).resolve().parents[1]
# chinook 数据库文件
DB_PATH = BACKEND_DIR / "data" / "chinook.db"
# sqlite 连接串(sqlalchemy DBParser 用)
DB_URL = f"sqlite:///{DB_PATH}"
# 历史记录持久化文件
HISTORY_DB_PATH = BACKEND_DIR / "data" / "history.db"


def _find_env_files() -> list[str]:
    """收集代码位置向上所有 .env(根在前、近的在后,后者覆盖前者)。

    目录链上可能存在多个 .env(如 邓帅/ 与项目根各一份):近的覆盖远的,
    缺失的键自动从更靠项目根的 .env 补全。
    """
    chain = [HW11_DIR, *HW11_DIR.parents]
    found = [str(d / ".env") for d in chain if (d / ".env").exists()]  # 近 → 远
    return list(reversed(found))


class Settings(BaseSettings):
    """LLM 凭据与 workflow 默认参数(均可用 .env 覆盖)。"""

    # 凭据(项目根 .env,密钥不写入代码)
    api_key: str = ""
    base_url: str = ""
    base_model: str = ""

    # workflow 参数
    max_rewrite_rounds: int = 3   # SQL 改写重试上限(第 5 步)
    row_limit: int = 50           # 执行结果行数截断(第 6 步)
    sample_rows: int = 3          # 选表/生成 prompt 中的表样例行数
    llm_timeout: float = 60.0     # 单次 LLM 调用超时(秒)

    model_config = SettingsConfigDict(
        env_file=_find_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
