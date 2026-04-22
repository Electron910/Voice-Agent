import structlog
from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import select, and_
from backend.models import Campaign, Appointment, Patient, Doctor, CampaignStatus, AppointmentStatus
from backend.database import async_session_factory

logger = structlog.get_logger()


class CampaignScheduler:
    async def create_reminder_campaign(self, hours_before: int = 24) -> dict:
        async with async_session_factory() as session:
            reminder_window_start = datetime.utcnow() + timedelta(hours=hours_before - 1)
            reminder_window_end = datetime.utcnow() + timedelta(hours=hours_before + 1)

            query = select(Appointment).where(
                and_(
                    Appointment.status == AppointmentStatus.SCHEDULED,
                    Appointment.date >= reminder_window_start,
                    Appointment.date <= reminder_window_end,
                )
            )
            result = await session.execute(query)
            appointments = result.scalars().all()

            target_patients = []
            for apt in appointments:
                patient = await session.get(Patient, apt.patient_id)
                doctor = await session.get(Doctor, apt.doctor_id)
                if patient and doctor:
                    target_patients.append({
                        "patient_id": str(apt.patient_id),
                        "patient_name": patient.name,
                        "patient_phone": patient.phone,
                        "preferred_language": patient.preferred_language,
                        "appointment_id": str(apt.id),
                        "doctor_name": doctor.name,
                        "specialization": doctor.specialization,
                        "date": str(apt.date.date()),
                        "time_slot": apt.time_slot,
                    })

            if not target_patients:
                return {"success": True, "message": "No appointments to remind", "count": 0}

            campaign = Campaign(
                name=f"Reminder Campaign - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                campaign_type="appointment_reminder",
                target_patients=target_patients,
                message_template="Reminder: You have an appointment with Dr. {doctor_name} on {date} at {time_slot}.",
                status=CampaignStatus.PENDING,
                scheduled_at=datetime.utcnow(),
            )
            session.add(campaign)
            await session.commit()
            await session.refresh(campaign)

            return {
                "success": True,
                "campaign_id": str(campaign.id),
                "target_count": len(target_patients),
                "targets": target_patients,
            }

    async def create_followup_campaign(self, days_after: int = 7) -> dict:
        async with async_session_factory() as session:
            followup_start = datetime.utcnow() - timedelta(days=days_after + 1)
            followup_end = datetime.utcnow() - timedelta(days=days_after - 1)

            query = select(Appointment).where(
                and_(
                    Appointment.status == AppointmentStatus.COMPLETED,
                    Appointment.date >= followup_start,
                    Appointment.date <= followup_end,
                )
            )
            result = await session.execute(query)
            appointments = result.scalars().all()

            target_patients = []
            for apt in appointments:
                patient = await session.get(Patient, apt.patient_id)
                doctor = await session.get(Doctor, apt.doctor_id)
                if patient and doctor:
                    target_patients.append({
                        "patient_id": str(apt.patient_id),
                        "patient_name": patient.name,
                        "patient_phone": patient.phone,
                        "preferred_language": patient.preferred_language,
                        "appointment_id": str(apt.id),
                        "doctor_name": doctor.name,
                        "specialization": doctor.specialization,
                        "date": str(apt.date.date()),
                        "time_slot": apt.time_slot,
                    })

            if not target_patients:
                return {"success": True, "message": "No follow-ups needed", "count": 0}

            campaign = Campaign(
                name=f"Follow-up Campaign - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                campaign_type="followup",
                target_patients=target_patients,
                message_template="Follow-up: How are you feeling after your visit with Dr. {doctor_name} on {date}? Would you like to schedule a follow-up?",
                status=CampaignStatus.PENDING,
                scheduled_at=datetime.utcnow(),
            )
            session.add(campaign)
            await session.commit()
            await session.refresh(campaign)

            return {
                "success": True,
                "campaign_id": str(campaign.id),
                "target_count": len(target_patients),
                "targets": target_patients,
            }

    async def get_campaign_targets(self, campaign_id: str) -> list:
        async with async_session_factory() as session:
            campaign = await session.get(Campaign, UUID(campaign_id))
            if not campaign:
                return []
            return campaign.target_patients or []

    async def update_campaign_status(self, campaign_id: str, status: CampaignStatus, results: dict = None):
        async with async_session_factory() as session:
            campaign = await session.get(Campaign, UUID(campaign_id))
            if campaign:
                campaign.status = status
                if results:
                    campaign.results = results
                if status == CampaignStatus.COMPLETED:
                    campaign.completed_at = datetime.utcnow()
                await session.commit()


campaign_scheduler = CampaignScheduler()