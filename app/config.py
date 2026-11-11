from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    library_path: Path = Path("./library")
    data_path: Path = Path("./data")
    auth_username: str = "admin"
    auth_password: str = "changeme"
    google_books_api_key: str = ""
    page_size: int = 50
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    user_agent: str = "opds-library/0.1 (self-hosted; https://localhost)"

    # 刮削：达到阈值或 ISBN 精确命中时自动写入未锁定字段
    scrape_auto_threshold: float = 90.0

    # 整理改名：默认关闭。开启前需把书库目录改为可写挂载。
    organize_enabled: bool = False
    organize_template: str = "{author}/{series}/{title}.{ext}"

    # 文件监听：inotify + 定时轮询兜底
    watch_enabled: bool = True
    watch_poll_seconds: int = 300
    watch_debounce_seconds: float = 2.0

    @property
    def database_path(self) -> Path:
        return self.data_path / "library.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.resolve()}"

    @property
    def covers_path(self) -> Path:
        return self.data_path / "covers"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
