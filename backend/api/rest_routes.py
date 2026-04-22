import structlog
from datetime import datetime
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database import get_db
from backend.models import Patient, Doctor, DoctorSchedule, Appointment, AppointmentStatus
from backend.agent.tools import check_availability, book_appointment, list_appointments

logger = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["api"])


class PatientCreate(BaseModel):
    name: str
    phone: str
    preferred_language: str = "en"


class DoctorCreate(BaseModel):
    name: str
    specialization: str
    available_days: list = []
    slot_duration_minutes: int = 30


class ScheduleCreate(BaseModel):
    doctor_id: str
    date: str
    available_slots: list


class BookingRequest(BaseModel):
    patient_id: str
    doctor_id: str
    date: str
    time_slot: str


@router.get("/health")
async def health():
    return {"status": "running", "timestamp": datetime.utcnow().isoformat()}


@router.post("/patients")
async def create_patient(data: PatientCreate, db: AsyncSession = Depends(get_db)):
    patient = Patient(
        name=data.name,
        phone=data.phone,
        preferred_language=data.preferred_language,
    )
    db.add(patient)
    await db.flush()
    await db.refresh(patient)
    return {"patient_id": str(patient.id), "name": patient.name, "phone": patient.phone}


@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, UUID(patient_id))
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_id": str(patient.id),
        "name": patient.name,
        "phone": patient.phone,
        "preferred_language": patient.preferred_language,
    }


@router.post("/doctors")
async def create_doctor(data: DoctorCreate, db: AsyncSession = Depends(get_db)):
    doctor = Doctor(
        name=data.name,
        specialization=data.specialization,
        available_days=data.available_days,
        slot_duration_minutes=data.slot_duration_minutes,
    )
    db.add(doctor)
    await db.flush()
    await db.refresh(doctor)
    return {"doctor_id": str(doctor.id), "name": doctor.name, "specialization": doctor.specialization}


@router.get("/doctors")
async def list_doctors(specialization: str = None, db: AsyncSession = Depends(get_db)):
    query = select(Doctor)
    if specialization:
        query = query.where(Doctor.specialization.ilike(f"%{specialization}%"))
    result = await db.execute(query)
    doctors = result.scalars().all()
    return {
        "doctors": [
            {
                "doctor_id": str(d.id),
                "name": d.name,
                "specialization": d.specialization,
                "slot_duration": d.slot_duration_minutes,
            }
            for d in doctors
        ]
    }


@router.post("/schedules")
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    target_date = datetime.strptime(data.date, "%Y-%m-%d")
    schedule = DoctorSchedule(
        doctor_id=UUID(data.doctor_id),
        date=target_date,
        available_slots=data.available_slots,
        booked_slots=[],
    )
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return {"schedule_id": str(schedule.id), "doctor_id": data.doctor_id, "date": data.date}


@router.get("/availability")
async def get_availability(doctor_id: str = None, specialization: str = None, date: str = None):
    result = await check_availability(
        doctor_id=doctor_id, specialization=specialization, date=date
    )
    return result


@router.post("/appointments/book")
async def book(data: BookingRequest):
    result = await book_appointment(
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        date=data.date,
        time_slot=data.time_slot,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/appointments/{patient_id}")
async def get_appointments(patient_id: str):
    result = await list_appointments(patient_id=patient_id)
    return result


@router.post("/seed")
async def seed_data(db: AsyncSession = Depends(get_db)):
    doctors_data = [
        {"name": "Dr. Priya Sharma", "specialization": "Cardiologist", "slot_duration_minutes": 30},
        {"name": "Dr. Rajesh Kumar", "specialization": "Dermatologist", "slot_duration_minutes": 20},
        {"name": "Dr. Anitha Rajan", "specialization": "General Physician", "slot_duration_minutes": 15},
        {"name": "Dr. Mohammed Ali", "specialization": "Orthopedic", "slot_duration_minutes": 30},
        {"name": "Dr. Lakshmi Iyer", "specialization": "Pediatrician", "slot_duration_minutes": 20},
    ]

    created_doctors = []
    for dd in doctors_data:
        doctor = Doctor(**dd, available_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        db.add(doctor)
        await db.flush()
        await db.refresh(doctor)
        created_doctors.append(doctor)

    slots = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
             "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]

    for doctor in created_doctors:
        for day_offset in range(14):
            d = datetime.utcnow().date()
            from datetime import timedelta
            target = d + timedelta(days=day_offset)
            if target.weekday() < 5:
                schedule = DoctorSchedule(
                    doctor_id=doctor.id,
                    date=datetime.combine(target, datetime.min.time()),
                    available_slots=slots,
                    booked_slots=[],
                )
                db.add(schedule)

    patients_data = [
        {"name": "Rahul Mehta", "phone": "+919876543210", "preferred_language": "en"},
        {"name": "Priya Krishnan", "phone": "+919876543211", "preferred_language": "ta"},
        {"name": "Amit Singh", "phone": "+919876543212", "preferred_language": "hi"},
    ]

    created_patients = []
    for pd in patients_data:
        patient = Patient(**pd)
        db.add(patient)
        await db.flush()
        await db.refresh(patient)
        created_patients.append(patient)

    await db.flush()

    return {
        "doctors": [{"id": str(d.id), "name": d.name, "specialization": d.specialization} for d in created_doctors],
        "patients": [{"id": str(p.id), "name": p.name, "phone": p.phone} for p in created_patients],
    }