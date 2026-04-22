import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from backend.scheduler.conflict_resolver import ConflictResolver


class TestConflictResolver:
    @pytest.fixture
    def resolver(self):
        return ConflictResolver()

    @pytest.mark.asyncio
    async def test_no_conflict(self, resolver):
        with patch("backend.scheduler.conflict_resolver.appointment_engine") as mock_engine:
            mock_engine.validate_booking = AsyncMock(return_value={"valid": True})

            result = await resolver.resolve(
                doctor_id="test-doc",
                requested_date="2025-01-15",
                requested_slot="10:00",
            )

            assert result["has_conflict"] is False
            assert result["original_slot_available"] is True

    @pytest.mark.asyncio
    async def test_conflict_with_alternatives(self, resolver):
        with patch("backend.scheduler.conflict_resolver.appointment_engine") as mock_engine:
            mock_engine.validate_booking = AsyncMock(return_value={
                "valid": False,
                "reason": "Slot already booked",
                "conflict": True,
            })
            mock_engine.get_next_available_slots = AsyncMock(return_value=[
                {
                    "doctor_id": "test-doc",
                    "doctor_name": "Dr. Test",
                    "date": "2025-01-15",
                    "available_slots": ["10:30", "11:00", "14:00"],
                },
            ])

            result = await resolver.resolve(
                doctor_id="test-doc",
                requested_date="2025-01-15",
                requested_slot="10:00",
            )

            assert result["has_conflict"] is True
            assert len(result["alternatives"]) > 0