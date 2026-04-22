from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Integer, JSON, Text, Boolean, Index
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid
import enum


class Base(DeclarativeBase):
    pass


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"
    NO_SHOW = "no_show"


class CampaignStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Patient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    preferred_language = Column(String(10), default="en")
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")
    interactions = relationship("Interaction", back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    specialization = Column(String(100), nullable=False, index=True)
    available_days = Column(JSON, default=list)
    slot_duration_minutes = Column(Integer, default=30)
    metadata_ = Column("metadata", JSON, default=dict)

    appointments = relationship("Appointment", back_populates="doctor")
    schedules = relationship("DoctorSchedule", back_populates="doctor")


class DoctorSchedule(Base):
    __tablename__ = "doctor_schedules"
    __table_args__ = (
        Index("idx_doctor_date", "doctor_id", "date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    available_slots = Column(JSON, default=list)
    booked_slots = Column(JSON, default=list)

    doctor = relationship("Doctor", back_populates="schedules")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("idx_doctor_datetime", "doctor_id", "date", "time_slot"),
        Index("idx_patient_status", "patient_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    time_slot = Column(String(10), nullable=False)
    status = Column(Enum(AppointmentStatus), default=AppointmentStatus.SCHEDULED)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    session_id = Column(String(100), nullable=False, index=True)
    direction = Column(String(10), default="inbound")
    language = Column(String(10), default="en")
    transcript = Column(JSON, default=list)
    actions_taken = Column(JSON, default=list)
    latency_log = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="interactions")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    campaign_type = Column(String(50), nullable=False)
    target_patients = Column(JSON, default=list)
    message_template = Column(Text, nullable=False)
    status = Column(Enum(CampaignStatus), default=CampaignStatus.PENDING)
    scheduled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    results = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)