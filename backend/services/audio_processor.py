import io
import struct
import numpy as np
import structlog

logger = structlog.get_logger()

SILENCE_THRESHOLD = 500
SILENCE_DURATION_MS = 800


class AudioProcessor:
    @staticmethod
    def create_wav_header(sample_rate: int = 16000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
        header = io.BytesIO()
        header.write(b"RIFF")
        header.write(struct.pack("<I", 0))
        header.write(b"WAVE")
        header.write(b"fmt ")
        header.write(struct.pack("<I", 16))
        header.write(struct.pack("<H", 1))
        header.write(struct.pack("<H", channels))
        header.write(struct.pack("<I", sample_rate))
        header.write(struct.pack("<I", sample_rate * channels * bits_per_sample // 8))
        header.write(struct.pack("<H", channels * bits_per_sample // 8))
        header.write(struct.pack("<H", bits_per_sample))
        header.write(b"data")
        header.write(struct.pack("<I", 0))
        return header.getvalue()

    @staticmethod
    def finalize_wav(header: bytes, audio_data: bytes) -> bytes:
        total_size = len(audio_data) + 36
        result = bytearray(header)
        struct.pack_into("<I", result, 4, total_size)
        struct.pack_into("<I", result, 40, len(audio_data))
        return bytes(result) + audio_data

    @staticmethod
    def detect_silence(audio_chunk: bytes, threshold: int = SILENCE_THRESHOLD) -> bool:
        if len(audio_chunk) < 2:
            return True
        samples = np.frombuffer(audio_chunk, dtype=np.int16)
        rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
        return rms < threshold

    @staticmethod
    def detect_speech_end(audio_buffer: list, sample_rate: int = 16000) -> bool:
        if not audio_buffer:
            return False

        bytes_per_ms = sample_rate * 2 // 1000
        silence_bytes = SILENCE_DURATION_MS * bytes_per_ms
        total_silence = 0

        for chunk in reversed(audio_buffer):
            if AudioProcessor.detect_silence(chunk):
                total_silence += len(chunk)
            else:
                break

        return total_silence >= silence_bytes

    @staticmethod
    def normalize_audio(audio_data: bytes, target_db: float = -20.0) -> bytes:
        samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float64)
        if len(samples) == 0:
            return audio_data
        rms = np.sqrt(np.mean(samples ** 2))
        if rms == 0:
            return audio_data
        current_db = 20 * np.log10(rms / 32768.0)
        gain = 10 ** ((target_db - current_db) / 20.0)
        samples = np.clip(samples * gain, -32768, 32767).astype(np.int16)
        return samples.tobytes()


audio_processor = AudioProcessor()