from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "VoiceAI Clinical Agent"
    debug: bool = False

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    gemini_api_key: str = ""

    deepgram_api_key: str = ""

    redis_url: str = "redis://localhost:6379/0"
    redis_session_ttl: int = 3600
    redis_memory_ttl: int = 86400 * 90

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/voiceai"

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    stt_provider: str = "deepgram"
    tts_provider: str = "deepgram"

    target_latency_ms: int = 450
    audio_sample_rate: int = 16000
    audio_channels: int = 1

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()