import asyncio
import httpx
import structlog
from backend.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class TTSService:
    def __init__(self):
        self._base_url = "https://api.deepgram.com/v1/speak"
        self._api_key = settings.deepgram_api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def synthesize(self, text: str, language: str = "en") -> bytes:
        model = "aura-asteria-en"
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }
        params = {
            "model": model,
            "encoding": "linear16",
            "sample_rate": settings.audio_sample_rate,
        }
        response = await self._client.post(
            self._base_url,
            headers=headers,
            params=params,
            json={"text": text},
        )
        response.raise_for_status()
        return response.content

    async def synthesize_streaming(self, text: str, language: str = "en"):
        if len(text) > 200:
            sentences = self._split_sentences(text)
            for sentence in sentences:
                async for chunk, is_first in self._stream_single(sentence):
                    yield chunk, is_first
        else:
            async for chunk, is_first in self._stream_single(text):
                yield chunk, is_first

    async def _stream_single(self, text: str):
        model = "aura-asteria-en"
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }
        params = {
            "model": model,
            "encoding": "linear16",
            "sample_rate": settings.audio_sample_rate,
        }

        async with self._client.stream(
            "POST",
            self._base_url,
            headers=headers,
            params=params,
            json={"text": text},
        ) as response:
            first_chunk = True
            async for chunk in response.aiter_bytes(chunk_size=8192):
                yield chunk, first_chunk
                first_chunk = False

    def _split_sentences(self, text: str) -> list:
        import re
        sentences = re.split(r'(?<=[.!?।])\s+', text)
        return [s.strip() for s in sentences if s.strip()]


tts_service = TTSService()