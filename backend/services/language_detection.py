from langdetect import detect, detect_langs
import structlog

logger = structlog.get_logger()

LANGUAGE_MAP = {
    "en": "en",
    "hi": "hi",
    "ta": "ta",
}

SUPPORTED_LANGUAGES = {"en", "hi", "ta"}


class LanguageDetectionService:
    @staticmethod
    def detect(text: str, fallback: str = "en") -> str:
        if not text or len(text.strip()) < 3:
            return fallback

        try:
            detected = detect(text)
            if detected in SUPPORTED_LANGUAGES:
                return detected

            lang_probs = detect_langs(text)
            for lp in lang_probs:
                if str(lp.lang) in SUPPORTED_LANGUAGES:
                    return str(lp.lang)

            return fallback
        except Exception as e:
            logger.warning("language_detection_failed", error=str(e), text=text[:50])
            return fallback

    @staticmethod
    def detect_with_confidence(text: str) -> dict:
        if not text or len(text.strip()) < 3:
            return {"language": "en", "confidence": 0.0}

        try:
            lang_probs = detect_langs(text)
            for lp in lang_probs:
                if str(lp.lang) in SUPPORTED_LANGUAGES:
                    return {"language": str(lp.lang), "confidence": lp.prob}
            return {"language": "en", "confidence": 0.0}
        except Exception:
            return {"language": "en", "confidence": 0.0}


language_detector = LanguageDetectionService()