import orjson
import redis.asyncio as redis
import structlog
from backend.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class PersistentMemory:
    def __init__(self):
        self._redis: redis.Redis = None

    async def initialize(self):
        self._redis = redis.from_url(
            settings.redis_url,
            decode_responses=False,
        )

    def _patient_key(self, patient_id: str) -> str:
        return f"patient_memory:{patient_id}"

    def _interaction_key(self, patient_id: str) -> str:
        return f"patient_interactions:{patient_id}"

    async def get_patient_profile(self, patient_id: str) -> dict:
        data = await self._redis.get(self._patient_key(patient_id))
        if data:
            return orjson.loads(data)
        return {
            "patient_id": patient_id,
            "preferred_language": "en",
            "preferred_doctors": [],
            "preferred_time_slots": [],
            "medical_notes": [],
            "interaction_count": 0,
            "last_interaction": None,
        }

    async def update_patient_profile(self, patient_id: str, updates: dict):
        profile = await self.get_patient_profile(patient_id)
        profile.update(updates)
        await self._redis.set(
            self._patient_key(patient_id),
            orjson.dumps(profile),
            ex=settings.redis_memory_ttl,
        )
        return profile

    async def record_interaction(self, patient_id: str, interaction: dict):
        key = self._interaction_key(patient_id)
        data = await self._redis.get(key)
        interactions = orjson.loads(data) if data else []
        interactions.append(interaction)
        if len(interactions) > 50:
            interactions = interactions[-50:]
        await self._redis.set(
            key,
            orjson.dumps(interactions),
            ex=settings.redis_memory_ttl,
        )

    async def get_recent_interactions(self, patient_id: str, limit: int = 5) -> list:
        key = self._interaction_key(patient_id)
        data = await self._redis.get(key)
        if data:
            interactions = orjson.loads(data)
            return interactions[-limit:]
        return []

    async def update_language_preference(self, patient_id: str, language: str):
        await self.update_patient_profile(patient_id, {"preferred_language": language})

    async def add_preferred_doctor(self, patient_id: str, doctor_id: str, doctor_name: str):
        profile = await self.get_patient_profile(patient_id)
        prefs = profile.get("preferred_doctors", [])
        entry = {"doctor_id": doctor_id, "doctor_name": doctor_name}
        if entry not in prefs:
            prefs.append(entry)
            if len(prefs) > 10:
                prefs = prefs[-10:]
            await self.update_patient_profile(patient_id, {"preferred_doctors": prefs})

    async def close(self):
        if self._redis:
            await self._redis.close()


persistent_memory = PersistentMemory()