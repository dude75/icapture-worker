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

    WORKERS: int = 2
    WORKER_QUEUE_SIZE: int = 0
    ENABLED_CONNECTORS: str = "jitsi"
    CAPTURE_FINALIZE_TIMEOUT_SEC: float = 120
    CAPTURE_PCM_SPOOL_INTERVAL_SEC: float = 5.0
    MAX_CAPTURE_DURATION_SEC: float = 14400
    TASK_TTL_SEC: int = 3600
    DEFAULT_BOT_DISPLAY_NAME: str = "Transcription Bot"
    LOG_LEVEL: str = "info"
    PLAYWRIGHT_HEADLESS: bool = True

    ARTIFACT_SAMPLE_RATE: int = 44100
    FFMPEG_MP3_VBR_QUALITY: int = 2

    @field_validator("WORKERS")
    @classmethod
    def _positive_workers(cls, value: int) -> int:
        if value < 1:
            raise ValueError("WORKERS must be >= 1")
        return value

    @field_validator("WORKER_QUEUE_SIZE")
    @classmethod
    def _non_negative_queue(cls, value: int) -> int:
        if value < 0:
            raise ValueError("WORKER_QUEUE_SIZE must be >= 0")
        return value

    @property
    def enabled_connectors(self) -> set[str]:
        parts = [item.strip().lower() for item in self.ENABLED_CONNECTORS.split(",")]
        return {item for item in parts if item}

    @field_validator("MAX_CAPTURE_DURATION_SEC")
    @classmethod
    def _non_negative_capture_duration(cls, value: float) -> float:
        if value < 0:
            raise ValueError("MAX_CAPTURE_DURATION_SEC must be >= 0")
        return value

    @field_validator("CAPTURE_PCM_SPOOL_INTERVAL_SEC")
    @classmethod
    def _positive_pcm_spool_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("CAPTURE_PCM_SPOOL_INTERVAL_SEC must be > 0")
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
