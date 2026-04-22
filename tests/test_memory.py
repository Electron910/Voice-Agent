import pytest
from unittest.mock import AsyncMock, patch
import orjson


class TestSessionMemory:
    @pytest.mark.asyncio
    async def test_default_session(self):
        from backend.memory.session_memory import SessionMemory
        memory = SessionMemory()
        memory._redis = AsyncMock()
        memory._redis.get = AsyncMock(return_value=None)

        session = await memory.get_session("test-id")

        assert session["session_id"] == "test-id"
        assert session["language"] == "en"
        assert session["current_intent"] is None
        assert session["conversation_state"] == "greeting"
        assert session["turn_history"] == []

    @pytest.mark.asyncio
    async def test_update_session(self):
        from backend.memory.session_memory import SessionMemory
        memory = SessionMemory()
        memory._redis = AsyncMock()

        existing = {
            "session_id": "test-id",
            "language": "en",
            "current_intent": None,
            "conversation_state": "greeting",
            "collected_slots": {},
            "turn_history": [],
            "tool_results": [],
            "interruption_flag": False,
            "patient_id": None,
            "pending_confirmations": [],
        }
        memory._redis.get = AsyncMock(return_value=orjson.dumps(existing))
        memory._redis.set = AsyncMock()

        result = await memory.update_session("test-id", {"language": "hi", "current_intent": "book"})

        assert result["language"] == "hi"
        assert result["current_intent"] == "book"

    @pytest.mark.asyncio
    async def test_append_turn_limits_history(self):
        from backend.memory.session_memory import SessionMemory
        memory = SessionMemory()
        memory._redis = AsyncMock()

        existing = {
            "session_id": "test-id",
            "language": "en",
            "current_intent": None,
            "conversation_state": "greeting",
            "collected_slots": {},
            "turn_history": [{"role": "user", "content": f"msg-{i}"} for i in range(25)],
            "tool_results": [],
            "interruption_flag": False,
            "patient_id": None,
            "pending_confirmations": [],
        }
        memory._redis.get = AsyncMock(return_value=orjson.dumps(existing))
        memory._redis.set = AsyncMock()

        await memory.append_turn("test-id", "user", "new message")

        call_args = memory._redis.set.call_args
        saved_data = orjson.loads(call_args[0][1])
        assert len(saved_data["turn_history"]) <= 20


class TestPersistentMemory:
    @pytest.mark.asyncio
    async def test_default_profile(self):
        from backend.memory.persistent_memory import PersistentMemory
        memory = PersistentMemory()
        memory._redis = AsyncMock()
        memory._redis.get = AsyncMock(return_value=None)

        profile = await memory.get_patient_profile("test-patient")

        assert profile["patient_id"] == "test-patient"
        assert profile["preferred_language"] == "en"
        assert profile["interaction_count"] == 0

    @pytest.mark.asyncio
    async def test_record_interaction_limits(self):
        from backend.memory.persistent_memory import PersistentMemory
        memory = PersistentMemory()
        memory._redis = AsyncMock()

        existing = [{"id": i} for i in range(55)]
        memory._redis.get = AsyncMock(return_value=orjson.dumps(existing))
        memory._redis.set = AsyncMock()

        await memory.record_interaction("test-patient", {"id": 999})

        call_args = memory._redis.set.call_args
        saved = orjson.loads(call_args[0][1])
        assert len(saved) <= 50