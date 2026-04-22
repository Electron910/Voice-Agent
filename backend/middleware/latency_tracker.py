import time
import structlog
from dataclasses import dataclass, field
from typing import Optional

logger = structlog.get_logger()


@dataclass
class LatencyBreakdown:
    session_id: str
    speech_end_timestamp: float = 0.0
    stt_start: float = 0.0
    stt_end: float = 0.0
    lang_detect_start: float = 0.0
    lang_detect_end: float = 0.0
    agent_start: float = 0.0
    agent_end: float = 0.0
    tool_exec_start: float = 0.0
    tool_exec_end: float = 0.0
    tts_start: float = 0.0
    tts_first_byte: float = 0.0
    tts_end: float = 0.0
    first_audio_response: float = 0.0
    stages: dict = field(default_factory=dict)

    def mark(self, stage: str, phase: str = "start"):
        ts = time.perf_counter() * 1000
        key = f"{stage}_{phase}"
        setattr(self, key.replace("-", "_"), ts) if hasattr(self, key.replace("-", "_")) else None
        self.stages[key] = ts
        return ts

    @property
    def stt_latency(self) -> float:
        if self.stt_end and self.stt_start:
            return self.stt_end - self.stt_start
        return 0.0

    @property
    def agent_latency(self) -> float:
        if self.agent_end and self.agent_start:
            return self.agent_end - self.agent_start
        return 0.0

    @property
    def tts_latency(self) -> float:
        if self.tts_first_byte and self.tts_start:
            return self.tts_first_byte - self.tts_start
        return 0.0

    @property
    def tool_latency(self) -> float:
        if self.tool_exec_end and self.tool_exec_start:
            return self.tool_exec_end - self.tool_exec_start
        return 0.0

    @property
    def total_latency(self) -> float:
        if self.first_audio_response and self.speech_end_timestamp:
            return self.first_audio_response - self.speech_end_timestamp
        return 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "total_ms": round(self.total_latency, 2),
            "stt_ms": round(self.stt_latency, 2),
            "agent_ms": round(self.agent_latency, 2),
            "tool_ms": round(self.tool_latency, 2),
            "tts_first_byte_ms": round(self.tts_latency, 2),
            "stages": {k: round(v, 2) for k, v in self.stages.items()},
            "under_target": self.total_latency < 450 if self.total_latency > 0 else None,
        }

    def log(self):
        data = self.to_dict()
        if data["under_target"]:
            logger.info("latency_report", **data)
        else:
            logger.warning("latency_exceeded", **data)
        return data


class LatencyTracker:
    def __init__(self):
        self._sessions: dict[str, LatencyBreakdown] = {}

    def create(self, session_id: str) -> LatencyBreakdown:
        breakdown = LatencyBreakdown(session_id=session_id)
        self._sessions[session_id] = breakdown
        return breakdown

    def get(self, session_id: str) -> Optional[LatencyBreakdown]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)


latency_tracker = LatencyTracker()