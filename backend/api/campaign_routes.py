import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from backend.scheduler.campaign_scheduler import campaign_scheduler
from backend.agent.orchestrator import agent_orchestrator
from backend.memory.memory_manager import memory_manager

logger = structlog.get_logger()
router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    campaign_type: str
    hours_before: int = 24
    days_after: int = 7


class OutboundCallSimulation(BaseModel):
    campaign_id: str
    patient_index: int = 0
    patient_response: str = ""


@router.post("/create")
async def create_campaign(data: CampaignCreate):
    if data.campaign_type == "reminder":
        result = await campaign_scheduler.create_reminder_campaign(hours_before=data.hours_before)
    elif data.campaign_type == "followup":
        result = await campaign_scheduler.create_followup_campaign(days_after=data.days_after)
    else:
        return {"success": False, "error": "Invalid campaign type. Use 'reminder' or 'followup'."}
    return result


@router.post("/simulate-outbound")
async def simulate_outbound_call(data: OutboundCallSimulation):
    targets = await campaign_scheduler.get_campaign_targets(data.campaign_id)

    if not targets or data.patient_index >= len(targets):
        return {"success": False, "error": "No target patient found at given index"}

    target = targets[data.patient_index]
    patient_id = target["patient_id"]
    language = target.get("preferred_language", "en")

    session_id = f"outbound-{data.campaign_id}-{patient_id}"

    await memory_manager.session.update_session(session_id, {
        "patient_id": patient_id,
        "language": language,
        "conversation_state": "outbound_greeting",
    })

    if not data.patient_response:
        initial_prompt = f"You are initiating an outbound call to {target['patient_name']}. Greet them and remind them about their appointment with {target['doctor_name']} on {target['date']} at {target['time_slot']}."
        outbound_context = {
            "campaign_purpose": "appointment reminder",
            "campaign_type": "reminder",
            "appointment_details": f"Dr. {target['doctor_name']} on {target['date']} at {target['time_slot']}",
        }
    else:
        initial_prompt = data.patient_response
        outbound_context = {
            "campaign_purpose": "appointment reminder",
            "campaign_type": "reminder",
            "appointment_details": f"Dr. {target['doctor_name']} on {target['date']} at {target['time_slot']}",
        }

    result = await agent_orchestrator.process_turn(
        session_id=session_id,
        patient_id=patient_id,
        user_text=initial_prompt,
        detected_language=language,
        is_outbound=True,
        outbound_context=outbound_context,
    )

    return {
        "success": True,
        "session_id": session_id,
        "patient": target,
        "agent_response": result.get("response_text"),
        "intent": result.get("intent"),
        "reasoning": result.get("reasoning"),
        "tool_calls": result.get("tool_calls", []),
        "tool_results": result.get("tool_results", []),
    }


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str):
    targets = await campaign_scheduler.get_campaign_targets(campaign_id)
    return {"campaign_id": campaign_id, "targets": targets, "count": len(targets)}