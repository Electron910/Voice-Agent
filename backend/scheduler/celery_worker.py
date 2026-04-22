from celery import Celery
from celery.schedules import crontab
from backend.config import get_settings

settings = get_settings()

celery_app = Celery(
    "voiceai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.conf.beat_schedule = {
    "reminder-campaign-24h": {
        "task": "backend.scheduler.celery_worker.run_reminder_campaign",
        "schedule": crontab(hour="8", minute="0"),
        "args": (24,),
    },
    "followup-campaign-7d": {
        "task": "backend.scheduler.celery_worker.run_followup_campaign",
        "schedule": crontab(hour="10", minute="0", day_of_week="1"),
        "args": (7,),
    },
}


@celery_app.task(bind=True, max_retries=3)
def run_reminder_campaign(self, hours_before: int = 24):
    import asyncio
    from backend.scheduler.campaign_scheduler import campaign_scheduler

    async def _run():
        result = await campaign_scheduler.create_reminder_campaign(hours_before=hours_before)
        return result

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3)
def run_followup_campaign(self, days_after: int = 7):
    import asyncio
    from backend.scheduler.campaign_scheduler import campaign_scheduler

    async def _run():
        result = await campaign_scheduler.create_followup_campaign(days_after=days_after)
        return result

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3)
def execute_campaign_calls(self, campaign_id: str):
    import asyncio
    from backend.scheduler.campaign_scheduler import campaign_scheduler
    from backend.agent.orchestrator import agent_orchestrator
    from backend.memory.memory_manager import memory_manager
    from backend.models import CampaignStatus

    async def _run():
        await memory_manager.initialize()

        targets = await campaign_scheduler.get_campaign_targets(campaign_id)
        if not targets:
            return {"status": "no_targets"}

        await campaign_scheduler.update_campaign_status(campaign_id, CampaignStatus.IN_PROGRESS)

        results = []
        for target in targets:
            patient_id = target["patient_id"]
            language = target.get("preferred_language", "en")
            session_id = f"campaign-{campaign_id}-{patient_id}"

            await memory_manager.session.update_session(session_id, {
                "patient_id": patient_id,
                "language": language,
                "conversation_state": "outbound_greeting",
            })

            outbound_context = {
                "campaign_purpose": "appointment reminder",
                "campaign_type": "reminder",
                "appointment_details": f"Dr. {target.get('doctor_name', 'N/A')} on {target.get('date', 'N/A')} at {target.get('time_slot', 'N/A')}",
            }

            greeting = f"Initiate outbound reminder call for {target.get('patient_name', 'Patient')} about appointment with Dr. {target.get('doctor_name', '')} on {target.get('date', '')} at {target.get('time_slot', '')}."

            result = await agent_orchestrator.process_turn(
                session_id=session_id,
                patient_id=patient_id,
                user_text=greeting,
                detected_language=language,
                is_outbound=True,
                outbound_context=outbound_context,
            )

            results.append({
                "patient_id": patient_id,
                "patient_name": target.get("patient_name"),
                "response": result.get("response_text", ""),
                "intent": result.get("intent"),
                "status": "called",
            })

        await campaign_scheduler.update_campaign_status(
            campaign_id,
            CampaignStatus.COMPLETED,
            results={"calls": results, "total": len(results)},
        )

        await memory_manager.close()
        return {"status": "completed", "calls": len(results)}

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=120 * (self.request.retries + 1))