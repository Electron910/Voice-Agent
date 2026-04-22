import asyncio
import structlog
from deepgram import DeepgramClient, PrerecordedOptions, LiveTranscriptionEvents, LiveOptions
from backend.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class STTService:
    def __init__(self):
        self._client = DeepgramClient(settings.deepgram_api_key)

    async def transcribe_audio(self, audio_bytes: bytes, language: str = "en") -> dict:
        lang_map = {
            "en": "en-IN",
            "hi": "hi",
            "ta": "ta",
        }

        options = PrerecordedOptions(
            model="nova-2",
            language=lang_map.get(language, "en-IN"),
            smart_format=True,
            detect_language=True,
            punctuate=True,
        )

        source = {"buffer": audio_bytes, "mimetype": "audio/wav"}

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.listen.rest.v("1").transcribe_file(source, options)
        )

        result = response.to_dict()
        channel = result.get("results", {}).get("channels", [{}])[0]
        alternatives = channel.get("alternatives", [{}])

        if not alternatives:
            return {"text": "", "confidence": 0.0, "detected_language": language}

        best = alternatives[0]
        detected_lang = channel.get("detected_language", language)

        return {
            "text": best.get("transcript", ""),
            "confidence": best.get("confidence", 0.0),
            "detected_language": detected_lang,
        }

    def create_live_connection(self, language: str = "en"):
        lang_map = {
            "en": "en-IN",
            "hi": "hi",
            "ta": "ta",
        }

        options = LiveOptions(
            model="nova-2",
            language=lang_map.get(language, "en-IN"),
            smart_format=True,
            interim_results=True,
            utterance_end_ms=1000,
            vad_events=True,
            endpointing=300,
        )

        return self._client.listen.live.v("1").open(options)


stt_service = STTService()