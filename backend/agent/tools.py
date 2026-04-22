import structlog
from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models import Appointment, Doctor, DoctorSchedule, Patient, AppointmentStatus
from backend.database import async_session_factory

logger = structlog.get_logger()


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name: str, func, description: str, parameters: dict):
        self._tools[name] = {
            "function": func,
            "description": description,
            "parameters": parameters,
        }

    def get(self, name: str):
        return self._tools.get(name)

    def list_tools(self) -> list:
        return [
            {"name": k, "description": v["description"], "parameters": v["parameters"]}
            for k, v in self._tools.items()
        ]


tool_registry = ToolRegistry()


async def check_availability(doctor_id: str = None, specialization: str = None, date: str = None) -> dict:
    async with async_session_factory() as session:
        try:
            if date:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            else:
                target_date = datetime.utcnow().date() + timedelta(days=1)

            if target_date < datetime.utcnow().date():
                return {"success": False, "error": "Cannot check availability for past dates", "alternatives": []}

            query = select(Doctor)
            if doctor_id:
                query = query.where(Doctor.id == UUID(doctor_id))
            elif specialization:
                query = query.where(Doctor.specialization.ilike(f"%{specialization}%"))

            result = await session.execute(query)
            doctors = result.scalars().all()

            if not doctors:
                return {"success": False, "error": "No doctors found matching criteria", "doctors": []}

            availability = []
            for doc in doctors:
                schedule_query = select(DoctorSchedule).where(
                    and_(
                        DoctorSchedule.doctor_id == doc.id,
                        DoctorSchedule.date == datetime.combine(target_date, datetime.min.time()),
                    )
                )
                sched_result = await session.execute(schedule_query)
                schedule = sched_result.scalar_one_or_none()

                if schedule:
                    booked = set(schedule.booked_slots or [])
                    available = [s for s in (schedule.available_slots or []) if s not in booked]
                else:
                    available = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
                                 "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]

                availability.append({
                    "doctor_id": str(doc.id),
                    "doctor_name": doc.name,
                    "specialization": doc.specialization,
                    "date": str(target_date),
                    "available_slots": available,
                })

            return {"success": True, "availability": availability}
        except Exception as e:
            logger.error("check_availability_error", error=str(e))
            return {"success": False, "error": str(e)}


async def book_appointment(patient_id: str, doctor_id: str, date: str, time_slot: str) -> dict:
    async with async_session_factory() as session:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d")
            now = datetime.utcnow()

            slot_time = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %H:%M")
            if slot_time < now:
                return {"success": False, "error": "Cannot book appointments in the past"}

            conflict_query = select(Appointment).where(
                and_(
                    Appointment.doctor_id == UUID(doctor_id),
                    Appointment.date == target_date,
                    Appointment.time_slot == time_slot,
                    Appointment.status == AppointmentStatus.SCHEDULED,
                )
            )
            conflict_result = await session.execute(conflict_query)
            existing = conflict_result.scalar_one_or_none()

            if existing:
                avail = await check_availability(doctor_id=doctor_id, date=date)
                alternatives = []
                if avail.get("success") and avail.get("availability"):
                    alternatives = avail["availability"][0].get("available_slots", [])[:3]
                return {
                    "success": False,
                    "error": "This slot is already booked",
                    "conflict": True,
                    "alternative_slots": alternatives,
                }

            patient_conflict = select(Appointment).where(
                and_(
                    Appointment.patient_id == UUID(patient_id),
                    Appointment.date == target_date,
                    Appointment.time_slot == time_slot,
                    Appointment.status == AppointmentStatus.SCHEDULED,
                )
            )
            p_result = await session.execute(patient_conflict)
            p_existing = p_result.scalar_one_or_none()

            if p_existing:
                return {
                    "success": False,
                    "error": "You already have an appointment at this time",
                    "existing_appointment_id": str(p_existing.id),
                }

            appointment = Appointment(
                patient_id=UUID(patient_id),
                doctor_id=UUID(doctor_id),
                date=target_date,
                time_slot=time_slot,
                status=AppointmentStatus.SCHEDULED,
            )
            session.add(appointment)
            await session.commit()
            await session.refresh(appointment)

            doctor = await session.get(Doctor, UUID(doctor_id))

            schedule_query = select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.doctor_id == UUID(doctor_id),
                    DoctorSchedule.date == target_date,
                )
            )
            sched_result = await session.execute(schedule_query)
            schedule = sched_result.scalar_one_or_none()
            if schedule:
                booked = list(schedule.booked_slots or [])
                booked.append(time_slot)
                schedule.booked_slots = booked
                await session.commit()

            return {
                "success": True,
                "appointment_id": str(appointment.id),
                "doctor_name": doctor.name if doctor else "Unknown",
                "date": date,
                "time_slot": time_slot,
                "status": "scheduled",
            }
        except Exception as e:
            logger.error("book_appointment_error", error=str(e))
            await session.rollback()
            return {"success": False, "error": str(e)}


async def cancel_appointment(appointment_id: str) -> dict:
    async with async_session_factory() as session:
        try:
            appointment = await session.get(Appointment, UUID(appointment_id))
            if not appointment:
                return {"success": False, "error": "Appointment not found"}

            if appointment.status == AppointmentStatus.CANCELLED:
                return {"success": False, "error": "Appointment is already cancelled"}

            appointment.status = AppointmentStatus.CANCELLED
            appointment.updated_at = datetime.utcnow()

            schedule_query = select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.doctor_id == appointment.doctor_id,
                    DoctorSchedule.date == appointment.date,
                )
            )
            sched_result = await session.execute(schedule_query)
            schedule = sched_result.scalar_one_or_none()
            if schedule and appointment.time_slot in (schedule.booked_slots or []):
                booked = list(schedule.booked_slots)
                booked.remove(appointment.time_slot)
                schedule.booked_slots = booked

            await session.commit()
            return {
                "success": True,
                "appointment_id": appointment_id,
                "status": "cancelled",
            }
        except Exception as e:
            logger.error("cancel_appointment_error", error=str(e))
            await session.rollback()
            return {"success": False, "error": str(e)}


async def reschedule_appointment(appointment_id: str, new_date: str, new_time_slot: str) -> dict:
    async with async_session_factory() as session:
        try:
            appointment = await session.get(Appointment, UUID(appointment_id))
            if not appointment:
                return {"success": False, "error": "Appointment not found"}

            cancel_result = await cancel_appointment(appointment_id)
            if not cancel_result["success"]:
                return cancel_result

            book_result = await book_appointment(
                patient_id=str(appointment.patient_id),
                doctor_id=str(appointment.doctor_id),
                date=new_date,
                time_slot=new_time_slot,
            )

            if book_result["success"]:
                return {
                    "success": True,
                    "old_appointment_id": appointment_id,
                    "new_appointment_id": book_result["appointment_id"],
                    "new_date": new_date,
                    "new_time_slot": new_time_slot,
                    "status": "rescheduled",
                }
            return book_result
        except Exception as e:
            logger.error("reschedule_error", error=str(e))
            return {"success": False, "error": str(e)}


async def list_appointments(patient_id: str) -> dict:
    async with async_session_factory() as session:
        try:
            query = (
                select(Appointment)
                .where(
                    and_(
                        Appointment.patient_id == UUID(patient_id),
                        Appointment.status == AppointmentStatus.SCHEDULED,
                        Appointment.date >= datetime.utcnow(),
                    )
                )
                .order_by(Appointment.date)
            )
            result = await session.execute(query)
            appointments = result.scalars().all()

            items = []
            for apt in appointments:
                doctor = await session.get(Doctor, apt.doctor_id)
                items.append({
                    "appointment_id": str(apt.id),
                    "doctor_name": doctor.name if doctor else "Unknown",
                    "specialization": doctor.specialization if doctor else "",
                    "date": str(apt.date.date()),
                    "time_slot": apt.time_slot,
                    "status": apt.status.value,
                })

            return {"success": True, "appointments": items, "count": len(items)}
        except Exception as e:
            logger.error("list_appointments_error", error=str(e))
            return {"success": False, "error": str(e)}


async def search_doctors(specialization: str) -> dict:
    async with async_session_factory() as session:
        try:
            query = select(Doctor).where(
                Doctor.specialization.ilike(f"%{specialization}%")
            )
            result = await session.execute(query)
            doctors = result.scalars().all()

            return {
                "success": True,
                "doctors": [
                    {
                        "doctor_id": str(d.id),
                        "name": d.name,
                        "specialization": d.specialization,
                        "slot_duration": d.slot_duration_minutes,
                    }
                    for d in doctors
                ],
            }
        except Exception as e:
            logger.error("search_doctors_error", error=str(e))
            return {"success": False, "error": str(e)}


tool_registry.register(
    "check_availability",
    check_availability,
    "Check doctor availability for a given date and specialization",
    {"doctor_id": "optional string", "specialization": "optional string", "date": "YYYY-MM-DD"},
)

tool_registry.register(
    "book_appointment",
    book_appointment,
    "Book a clinical appointment",
    {"patient_id": "string", "doctor_id": "string", "date": "YYYY-MM-DD", "time_slot": "HH:MM"},
)

tool_registry.register(
    "cancel_appointment",
    cancel_appointment,
    "Cancel an existing appointment",
    {"appointment_id": "string"},
)

tool_registry.register(
    "reschedule_appointment",
    reschedule_appointment,
    "Reschedule an appointment to a new date/time",
    {"appointment_id": "string", "new_date": "YYYY-MM-DD", "new_time_slot": "HH:MM"},
)

tool_registry.register(
    "list_appointments",
    list_appointments,
    "List upcoming appointments for a patient",
    {"patient_id": "string"},
)

tool_registry.register(
    "search_doctors",
    search_doctors,
    "Search for doctors by specialization",
    {"specialization": "string"},
)