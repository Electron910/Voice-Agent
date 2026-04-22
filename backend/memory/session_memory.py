import orjson
import redis.asyncio as redis
import structlog
from backend.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class SessionMemory:
    def __init__(self):
        self._redis: redis.Redis = None

    async def initialize(self):
        self._redis = redis.from_url(
            settings.redis_url,
            decode_responses=False,
        )

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def get_session(self, session_id: str) -> dict:
        data = await self._redis.get(self._key(session_id))
        if data:
            return orjson.loads(data)
        return self._default_session(session_id)

    def _default_session(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "patient_id": None,
            "language": "en",
            "current_intent": None,
            "conversation_state": "greeting",
            "pending_confirmations": [],
            "collected_slots": {},
            "turn_history": [],
            "tool_results": [],
            "interruption_flag": False,
        }

    async def update_session(self, session_id: str, updates: dict):
        current = await self.get_session(session_id)
        current.update(updates)
        await self._redis.set(
            self._key(session_id),
            orjson.dumps(current),
            ex=settings.redis_session_ttl,
        )
        return current

    async def append_turn(self, session_id: str, role: str, content: str):
        session = await self.get_session(session_id)
        session["turn_history"].append({"role": role, "content": content})
        if len(session["turn_history"]) > 20:
            session["turn_history"] = session["turn_history"][-20:]
        await self._redis.set(
            self._key(session_id),
            orjson.dumps(session),
            ex=settings.redis_session_ttl,
        )

    async def set_slots(self, session_id: str, slots: dict):
        session = await self.get_session(session_id)
        session["collected_slots"].update(slots)
        await self._redis.set(
            self._key(session_id),
            orjson.dumps(session),
            ex=settings.redis_session_ttl,
        )

    async def clear_session(self, session_id: str):
        await self._redis.delete(self._key(session_id))

    async def set_interruption(self, session_id: str, flag: bool):
        await self.update_session(session_id, {"interruption_flag": flag})

    async def close(self):
        if self._redis:
            await self._redis.close()


session_memory = SessionMemory()