"""initial schema

Revision ID: 001
Revises:
Create Date: 2024-12-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "patients",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), unique=True, nullable=False),
        sa.Column("preferred_language", sa.String(10), server_default="en"),
        sa.Column("metadata", JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_patients_phone", "patients", ["phone"])

    op.create_table(
        "doctors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("specialization", sa.String(100), nullable=False),
        sa.Column("available_days", JSON, server_default="[]"),
        sa.Column("slot_duration_minutes", sa.Integer, server_default="30"),
        sa.Column("metadata", JSON, server_default="{}"),
    )
    op.create_index("ix_doctors_specialization", "doctors", ["specialization"])

    op.create_table(
        "doctor_schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("doctor_id", UUID(as_uuid=True), sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("date", sa.DateTime, nullable=False),
        sa.Column("available_slots", JSON, server_default="[]"),
        sa.Column("booked_slots", JSON, server_default="[]"),
    )
    op.create_index("idx_doctor_date", "doctor_schedules", ["doctor_id", "date"])

    op.create_table(
        "appointments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("doctor_id", UUID(as_uuid=True), sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("date", sa.DateTime, nullable=False),
        sa.Column("time_slot", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), server_default="scheduled"),
        sa.Column("notes", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_doctor_datetime", "appointments", ["doctor_id", "date", "time_slot"])
    op.create_index("idx_patient_status", "appointments", ["patient_id", "status"])

    op.create_table(
        "interactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", UUID(as_uuid=True), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(10), server_default="inbound"),
        sa.Column("language", sa.String(10), server_default="en"),
        sa.Column("transcript", JSON, server_default="[]"),
        sa.Column("actions_taken", JSON, server_default="[]"),
        sa.Column("latency_log", JSON, server_default="[]"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_interactions_session_id", "interactions", ["session_id"])

    op.create_table(
        "campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("campaign_type", sa.String(50), nullable=False),
        sa.Column("target_patients", JSON, server_default="[]"),
        sa.Column("message_template", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("results", JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("campaigns")
    op.drop_table("interactions")
    op.drop_table("appointments")
    op.drop_table("doctor_schedules")
    op.drop_table("doctors")
    op.drop_table("patients")