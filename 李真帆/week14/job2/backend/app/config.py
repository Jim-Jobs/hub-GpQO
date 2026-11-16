from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-chat"
    vision_model: str = "deepseek-flash"

    # Embedding
    embedding_provider: str = "api"  # api | local
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    local_embed_model: str = "BAAI/bge-small-zh-v1.5"

    # 基础设施
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "rag.doc.parse"
    redis_url: str = "redis://localhost:6379/0"

    # MineRU
    mineru_bin: str = "mineru-kit"
    mineru_tier: str = "advanced"
    mineru_timeout: int = 600

    # RAG 参数
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 3
    chat_max_tokens: int = 2048

    data_dir: Path = Path("data")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def chat_images_dir(self) -> Path:
        return self.data_dir / "chat_images"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rag.db"

    @property
    def embed_base_url(self) -> str:
        return self.embedding_base_url or self.deepseek_base_url

    @property
    def embed_api_key(self) -> str:
        return self.embedding_api_key or self.deepseek_api_key

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.parsed_dir, self.tmp_dir, self.chat_images_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
