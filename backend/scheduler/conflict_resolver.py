import structlog
from datetime import datetime, timedelta
from backend.scheduler.appointment_engine import appointment_engine

logger = structlog.get_logger()


class ConflictResolver:
    async def resolve(self, doctor_id: str, requested_date: str, requested_slot: str) -> dict:
        validation = await appointment_engine.validate_booking(doctor_id, requested_date, requested_slot)

        if validation["valid"]:
            return {
                "has_conflict": False,
                "original_slot_available": True,
            }

        alternatives = await self._find_alternatives(doctor_id, requested_date, requested_slot)

        return {
            "has_conflict": True,
            "reason": validation.get("reason", "Slot unavailable"),
            "alternatives": alternatives,
        }

    async def _find_alternatives(self, doctor_id: str, date: str, slot: str) -> list:
        alternatives = []

        same_day_slots = await appointment_engine.get_next_available_slots(
            doctor_id=doctor_id, days_ahead=1
        )
        for entry in same_day_slots:
            if entry["date"] == date:
                for available_slot in entry["available_slots"][:3]:
                    alternatives.append({
                        "date": date,
                        "time_slot": available_slot,
                        "type": "same_day",
                    })

        if len(alternatives) < 3:
            nearby_slots = await appointment_engine.get_next_available_slots(
                doctor_id=doctor_id, days_ahead=7
            )
            for entry in nearby_slots:
                if entry["date"] != date and entry["available_slots"]:
                    alternatives.append({
                        "date": entry["date"],
                        "time_slot": entry["available_slots"][0],
                        "type": "nearby_day",
                    })
                if len(alternatives) >= 5:
                    break

        return alternatives[:5]


conflict_resolver = ConflictResolver()