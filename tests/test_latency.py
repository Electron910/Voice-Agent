import pytest
import time
from backend.middleware.latency_tracker import LatencyBreakdown, LatencyTracker


class TestLatencyBreakdown:
    def test_total_latency_calculation(self):
        lb = LatencyBreakdown(session_id="test")
        lb.speech_end_timestamp = 1000.0
        lb.first_audio_response = 1400.0

        assert lb.total_latency == 400.0

    def test_under_target(self):
        lb = LatencyBreakdown(session_id="test")
        lb.speech_end_timestamp = 1000.0
        lb.first_audio_response = 1400.0

        report = lb.to_dict()
        assert report["under_target"] is True

    def test_over_target(self):
        lb = LatencyBreakdown(session_id="test")
        lb.speech_end_timestamp = 1000.0
        lb.first_audio_response = 1500.0

        report = lb.to_dict()
        assert report["under_target"] is False

    def test_stage_latencies(self):
        lb = LatencyBreakdown(session_id="test")
        lb.stt_start = 100.0
        lb.stt_end = 220.0
        lb.agent_start = 220.0
        lb.agent_end = 400.0
        lb.tts_start = 400.0
        lb.tts_first_byte = 480.0

        assert lb.stt_latency == 120.0
        assert lb.agent_latency == 180.0
        assert lb.tts_latency == 80.0

    def test_to_dict(self):
        lb = LatencyBreakdown(session_id="test-session")
        lb.speech_end_timestamp = 1000.0
        lb.stt_start = 1000.0
        lb.stt_end = 1120.0
        lb.agent_start = 1120.0
        lb.agent_end = 1300.0
        lb.tts_start = 1300.0
        lb.tts_first_byte = 1380.0
        lb.first_audio_response = 1380.0

        data = lb.to_dict()
        assert data["session_id"] == "test-session"
        assert data["total_ms"] == 380.0
        assert data["stt_ms"] == 120.0
        assert data["agent_ms"] == 180.0
        assert data["tts_first_byte_ms"] == 80.0
        assert data["under_target"] is True


class TestLatencyTracker:
    def test_create_and_get(self):
        tracker = LatencyTracker()
        lb = tracker.create("session-1")
        assert lb.session_id == "session-1"

        retrieved = tracker.get("session-1")
        assert retrieved is lb

    def test_remove(self):
        tracker = LatencyTracker()
        tracker.create("session-1")
        tracker.remove("session-1")
        assert tracker.get("session-1") is None

    def test_nonexistent_session(self):
        tracker = LatencyTracker()
        assert tracker.get("nonexistent") is None