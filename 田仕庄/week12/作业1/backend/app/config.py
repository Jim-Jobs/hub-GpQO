"""应用配置：从环境变量 / .env 读取。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek 多模态模型
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-flash"

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout: float = 60.0
    max_retries: int = 1            # 模型输出非法时的重试次数
    max_image_mb: int = 10          # 单张图片大小上限

    # 路径
    log_dir: Path = BASE_DIR / "logs"
    frontend_dir: Path = BASE_DIR / "frontend"

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_mb * 1024 * 1024


settings = Settings()
