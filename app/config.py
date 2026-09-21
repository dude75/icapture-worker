"""Process settings from `.env`."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    API_TOKEN: str = ""
    METRICS_TOKEN: str = ""

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DATA_DIR: str = "./data"
    SQLITE_PATH: str = "./data/tasks.db"
    LOG_DIR: str = "./data/logs"
    LOG_ENABLED: bool = True
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5
    METRICS_ENABLED: bool = True

    MAX_CONCURRENT_CAPTURES: int = 2
    MAX_CAPTURE_DURATION_SEC: float = 14400
    TASK_TTL_SEC: int = 3600
    DEFAULT_BOT_DISPLAY_NAME: str = "Transcription Bot"
    LOG_LEVEL: str = "info"

    JITSI_ENGINE_URL: str = "http://127.0.0.1:8001"
    JITSI_ENGINE_TIMEOUT_SEC: float = 120
    ARTIFACT_SAMPLE_RATE: int = 44100
    FFMPEG_MP3_VBR_QUALITY: int = 2

    @field_validator("MAX_CONCURRENT_CAPTURES")
    @classmethod
    def _positive_slots(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_CONCURRENT_CAPTURES must be >= 1")
        return value

    @field_validator("MAX_CAPTURE_DURATION_SEC")
    @classmethod
    def _non_negative_capture_duration(cls, value: float) -> float:
        if value < 0:
            raise ValueError("MAX_CAPTURE_DURATION_SEC must be >= 0")
        return value

    @field_validator("TASK_TTL_SEC")
    @classmethod
    def _non_negative_task_ttl(cls, value: int) -> int:
        if value < 0:
            raise ValueError("TASK_TTL_SEC must be >= 0")
        return value

    @field_validator("LOG_MAX_BYTES")
    @classmethod
    def _positive_log_max_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LOG_MAX_BYTES must be > 0")
        return value

    @field_validator("LOG_BACKUP_COUNT")
    @classmethod
    def _positive_log_backup_count(cls, value: int) -> int:
        if value < 1:
            raise ValueError("LOG_BACKUP_COUNT must be >= 1")
        return value

    @field_validator("ARTIFACT_SAMPLE_RATE")
    @classmethod
    def _allowed_artifact_sample_rate(cls, value: int) -> int:
        if value not in {44100}:
            raise ValueError("ARTIFACT_SAMPLE_RATE must be 44100")
        return value

    @field_validator("FFMPEG_MP3_VBR_QUALITY")
    @classmethod
    def _allowed_ffmpeg_quality(cls, value: int) -> int:
        if value < 0 or value > 9:
            raise ValueError("FFMPEG_MP3_VBR_QUALITY must be between 0 and 9")
        return value

    @property
    def artifacts_dir(self) -> str:
        return f"{self.DATA_DIR.rstrip('/')}/artifacts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
