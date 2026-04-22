import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from backend.agent.orchestrator import AgentOrchestrator
from backend.services.language_detection import language_detector


class TestLanguageDetection:
    def test_detect_english(self):
        result = language_detector.detect("I want to book an appointment with a cardiologist")
        assert result == "en"

    def test_detect_hindi(self):
        result = language_detector.detect("मुझे कल डॉक्टर से मिलना है")
        assert result == "hi"

    def test_detect_tamil(self):
        result = language_detector.detect("நாளை மருத்துவரை பார்க்க வேண்டும்")
        assert result == "ta"

    def test_detect_short_text_fallback(self):
        result = language_detector.detect("hi", fallback="en")
        assert result == "en"

    def test_detect_empty_text(self):
        result = language_detector.detect("")
        assert result == "en"

    def test_detect_with_confidence(self):
        result = language_detector.detect_with_confidence("I want to see the doctor tomorrow morning")
        assert result["language"] == "en"
        assert result["confidence"] > 0


class TestAgentOrchestrator:
    @pytest.fixture
    def orchestrator(self):
        return AgentOrchestrator()

    @pytest.mark.asyncio
    async def test_process_turn_returns_response(self, orchestrator):
        mock_reasoning = {
            "reasoning": "User wants to book appointment",
            "intent": "book",
            "tool_calls": [],
            "response_text": "Which doctor would you like to see?",
            "slots_extracted": {},
            "needs_confirmation": False,
            "conversation_state": "collecting_info",
        }

        with patch("backend.agent.orchestrator.reasoning_engine") as mock_engine, \
             patch("backend.agent.orchestrator.memory_manager") as mock_memory:

            mock_engine.reason = AsyncMock(return_value=mock_reasoning)
            mock_memory.session.update_session = AsyncMock(return_value={})
            mock_memory.session.get_session = AsyncMock(return_value={
                "session_id": "test",
                "language": "en",
                "current_intent": None,
                "conversation_state": "greeting",
                "collected_slots": {},
                "turn_history": [],
                "tool_results": [],
                "interruption_flag": False,
                "patient_id": None,
                "pending_confirmations": [],
            })
            mock_memory.session.set_slots = AsyncMock()
            mock_memory.build_context = AsyncMock(return_value={
                "session": {
                    "language": "en",
                    "current_intent": None,
                    "conversation_state": "greeting",
                    "collected_slots": {},
                    "turn_history": [],
                    "tool_results": [],
                },
                "patient_profile": {},
                "recent_interactions": [],
            })
            mock_memory.update_after_turn = AsyncMock()

            result = await orchestrator.process_turn(
                session_id="test-session",
                patient_id="test-patient",
                user_text="I want to book an appointment",
                detected_language="en",
            )

            assert result["response_text"] == "Which doctor would you like to see?"
            assert result["intent"] == "book"
            assert result["language"] == "en"

    @pytest.mark.asyncio
    async def test_tool_execution(self, orchestrator):
        mock_reasoning = {
            "reasoning": "Searching for cardiologists",
            "intent": "book",
            "tool_calls": [{"tool": "search_doctors", "parameters": {"specialization": "cardiologist"}}],
            "response_text": "Let me find cardiologists for you.",
            "slots_extracted": {"specialization": "cardiologist"},
            "needs_confirmation": False,
            "conversation_state": "collecting_info",
        }

        with patch("backend.agent.orchestrator.reasoning_engine") as mock_engine, \
             patch("backend.agent.orchestrator.memory_manager") as mock_memory, \
             patch("backend.agent.orchestrator.tool_registry") as mock_registry:

            mock_engine.reason = AsyncMock(return_value=mock_reasoning)
            mock_memory.session.update_session = AsyncMock(return_value={})
            mock_memory.session.get_session = AsyncMock(return_value={
                "session_id": "test",
                "language": "en",
                "current_intent": None,
                "conversation_state": "greeting",
                "collected_slots": {},
                "turn_history": [],
                "tool_results": [],
                "interruption_flag": False,
                "patient_id": None,
                "pending_confirmations": [],
            })
            mock_memory.session.set_slots = AsyncMock()
            mock_memory.build_context = AsyncMock(return_value={
                "session": {"language": "en", "current_intent": None, "conversation_state": "greeting", "collected_slots": {}, "turn_history": [], "tool_results": []},
                "patient_profile": {},
                "recent_interactions": [],
            })
            mock_memory.update_after_turn = AsyncMock()

            mock_tool = AsyncMock(return_value={"success": True, "doctors": [{"name": "Dr. Sharma", "doctor_id": "abc"}]})
            mock_registry.get.return_value = {
                "function": mock_tool,
                "description": "Search doctors",
                "parameters": {"specialization": "string"},
            }

            result = await orchestrator.process_turn(
                session_id="test-session",
                patient_id="test-patient",
                user_text="Find me a cardiologist",
                detected_language="en",
            )

            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool"] == "search_doctors"