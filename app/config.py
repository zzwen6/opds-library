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

    @property
    def database_path(self) -> Path:
        return self.data_path / "library.db"

    @property
    def database_url(self) -> str:
        # 四个斜杠：绝对路径 SQLite
        return f"sqlite:///{self.database_path.resolve()}"

    @property
    def covers_path(self) -> Path:
        return self.data_path / "covers"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
