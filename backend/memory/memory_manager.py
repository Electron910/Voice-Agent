import structlog
from datetime import datetime
from backend.memory.session_memory import session_memory
from backend.memory.persistent_memory import persistent_memory

logger = structlog.get_logger()


class MemoryManager:
    def __init__(self):
        self.session = session_memory
        self.persistent = persistent_memory

    async def initialize(self):
        await self.session.initialize()
        await self.persistent.initialize()

    async def build_context(self, session_id: str, patient_id: str = None) -> dict:
        session_data = await self.session.get_session(session_id)
        patient_profile = {}
        recent_interactions = []

        if patient_id:
            patient_profile = await self.persistent.get_patient_profile(patient_id)
            recent_interactions = await self.persistent.get_recent_interactions(patient_id, limit=3)

        return {
            "session": session_data,
            "patient_profile": patient_profile,
            "recent_interactions": recent_interactions,
        }

    async def update_after_turn(
        self,
        session_id: str,
        patient_id: str,
        user_text: str,
        agent_response: str,
        language: str,
        intent: str = None,
        actions: list = None,
    ):
        await self.session.append_turn(session_id, "user", user_text)
        await self.session.append_turn(session_id, "assistant", agent_response)

        if intent:
            await self.session.update_session(session_id, {"current_intent": intent})

        if patient_id:
            await self.persistent.update_language_preference(patient_id, language)
            interaction = {
                "session_id": session_id,
                "timestamp": datetime.utcnow().isoformat(),
                "user_text": user_text,
                "agent_response": agent_response[:200],
                "language": language,
                "intent": intent,
                "actions": actions or [],
            }
            await self.persistent.record_interaction(patient_id, interaction)
            await self.persistent.update_patient_profile(
                patient_id,
                {
                    "interaction_count": (
                        await self.persistent.get_patient_profile(patient_id)
                    ).get("interaction_count", 0) + 1,
                    "last_interaction": datetime.utcnow().isoformat(),
                },
            )

    async def close(self):
        await self.session.close()
        await self.persistent.close()


memory_manager = MemoryManager()