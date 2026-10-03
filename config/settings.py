"""Configuration and environment settings for Pharma DW & Agent."""
import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base paths
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    REPORTS_DIR: Path = BASE_DIR / "reports"

    # Database Configuration
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "pharma_dw"
    POSTGRES_USER: str = "dw_admin"
    POSTGRES_PASSWORD: str = "pharma_secure_pass_2025"
    DATABASE_URL: Optional[str] = None

    # Read-Only Semantic Role (for Text-to-SQL Agent)
    RO_POSTGRES_USER: str = "pharma_analyst_ro"
    RO_POSTGRES_PASSWORD: str = "ro_secure_analyst_2025"
    RO_DATABASE_URL: Optional[str] = None

    # Local fallback engine (DuckDB) for local zero-dependency testing
    USE_DUCKDB_FALLBACK: bool = False
    DUCKDB_PATH: Path = BASE_DIR / "data" / "pharma_dw.duckdb"

    # LLM Configuration
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    DEFAULT_LLM_PROVIDER: str = "openai"
    DEFAULT_LLM_MODEL: str = "gpt-4o-mini"

    # Pipeline & Alerts
    SLACK_WEBHOOK_URL: Optional[str] = None
    ALERT_EMAIL_RECIPIENT: str = "alerts@commercialops.pharma.internal"
    LOG_LEVEL: str = "INFO"

    def get_database_url(self, read_only: bool = False) -> str:
        if self.DATABASE_URL and not read_only:
            return self.DATABASE_URL
        if self.RO_DATABASE_URL and read_only:
            return self.RO_DATABASE_URL

        user = self.RO_POSTGRES_USER if read_only else self.POSTGRES_USER
        password = self.RO_POSTGRES_PASSWORD if read_only else self.POSTGRES_PASSWORD
        return f"postgresql://{user}:{password}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
