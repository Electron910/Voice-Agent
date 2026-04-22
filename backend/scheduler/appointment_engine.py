import structlog
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models import Appointment, Doctor, DoctorSchedule, AppointmentStatus
from backend.database import async_session_factory

logger = structlog.get_logger()


class AppointmentEngine:
    async def get_next_available_slots(
        self, doctor_id: str = None, specialization: str = None, days_ahead: int = 7
    ) -> list:
        async with async_session_factory() as session:
            query = select(Doctor)
            if doctor_id:
                query = query.where(Doctor.id == doctor_id)
            elif specialization:
                query = query.where(Doctor.specialization.ilike(f"%{specialization}%"))

            result = await session.execute(query)
            doctors = result.scalars().all()

            all_slots = []
            today = datetime.utcnow().date()

            for doctor in doctors:
                for day_offset in range(days_ahead):
                    check_date = today + timedelta(days=day_offset)
                    target_dt = datetime.combine(check_date, datetime.min.time())

                    sched_query = select(DoctorSchedule).where(
                        and_(
                            DoctorSchedule.doctor_id == doctor.id,
                            DoctorSchedule.date == target_dt,
                        )
                    )
                    sched_result = await session.execute(sched_query)
                    schedule = sched_result.scalar_one_or_none()

                    if schedule:
                        booked = set(schedule.booked_slots or [])
                        available = [s for s in (schedule.available_slots or []) if s not in booked]
                    else:
                        available = [
                            "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
                            "14:00", "14:30", "15:00", "15:30", "16:00", "16:30",
                        ]

                    if available:
                        all_slots.append({
                            "doctor_id": str(doctor.id),
                            "doctor_name": doctor.name,
                            "date": str(check_date),
                            "available_slots": available,
                        })

            return all_slots

    async def validate_booking(self, doctor_id: str, date: str, time_slot: str) -> dict:
        target_date = datetime.strptime(date, "%Y-%m-%d")
        slot_dt = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %H:%M")

        if slot_dt < datetime.utcnow():
            return {"valid": False, "reason": "Cannot book in the past"}

        if target_date.date() > (datetime.utcnow().date() + timedelta(days=90)):
            return {"valid": False, "reason": "Cannot book more than 90 days in advance"}

        async with async_session_factory() as session:
            doctor = await session.get(Doctor, doctor_id)
            if not doctor:
                return {"valid": False, "reason": "Doctor not found"}

            conflict_query = select(Appointment).where(
                and_(
                    Appointment.doctor_id == doctor_id,
                    Appointment.date == target_date,
                    Appointment.time_slot == time_slot,
                    Appointment.status == AppointmentStatus.SCHEDULED,
                )
            )
            result = await session.execute(conflict_query)
            if result.scalar_one_or_none():
                return {"valid": False, "reason": "Slot already booked", "conflict": True}

        return {"valid": True}


appointment_engine = AppointmentEngine()