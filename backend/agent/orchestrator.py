import asyncio
import time
import structlog
from backend.agent.reasoning import reasoning_engine
from backend.agent.tools import tool_registry
from backend.memory.memory_manager import memory_manager
from backend.services.language_detection import language_detector
from backend.middleware.latency_tracker import LatencyBreakdown

logger = structlog.get_logger()


class AgentOrchestrator:
    async def process_turn(
        self,
        session_id: str,
        patient_id: str,
        user_text: str,
        detected_language: str = None,
        latency: LatencyBreakdown = None,
        is_outbound: bool = False,
        outbound_context: dict = None,
    ) -> dict:
        if latency:
            latency.mark("agent", "start")

        if not detected_language:
            detected_language = language_detector.detect(user_text)

        await memory_manager.session.update_session(session_id, {
            "language": detected_language,
            "patient_id": patient_id,
        })

        context = await memory_manager.build_context(session_id, patient_id)
        context["session"]["language"] = detected_language

        if outbound_context:
            context.update(outbound_context)

        reasoning_result = await reasoning_engine.reason(
            user_text=user_text,
            context=context,
            is_outbound=is_outbound,
        )

        extracted_slots = reasoning_result.get("slots_extracted", {})
        if extracted_slots:
            await memory_manager.session.set_slots(session_id, extracted_slots)

        new_state = reasoning_result.get("conversation_state", "greeting")
        new_intent = reasoning_result.get("intent")

        await memory_manager.session.update_session(session_id, {
            "conversation_state": new_state,
            "current_intent": new_intent,
        })

        tool_results = []
        tool_calls = reasoning_result.get("tool_calls", [])

        if tool_calls:
            if latency:
                latency.mark("tool_exec", "start")
            try:
                tool_results = await asyncio.wait_for(
                    self._execute_tools(tool_calls, patient_id),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                tool_results = [{"success": False, "error": "Tool timed out"}]
            if latency:
                latency.mark("tool_exec", "end")

            if tool_results:
                tool_response = self._format_tool_results(reasoning_result, tool_results, detected_language)
                if tool_response:
                    reasoning_result["response_text"] = tool_response

        await memory_manager.session.append_turn(session_id, "user", user_text)
        await memory_manager.session.append_turn(session_id, "assistant", reasoning_result.get("response_text", ""))

        asyncio.create_task(self._background_update(
            session_id, patient_id, user_text,
            reasoning_result, detected_language, tool_calls, tool_results,
        ))

        if latency:
            latency.mark("agent", "end")

        return {
            "response_text": reasoning_result.get("response_text", ""),
            "intent": new_intent,
            "reasoning": reasoning_result.get("reasoning"),
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "language": detected_language,
            "conversation_state": new_state,
            "needs_confirmation": reasoning_result.get("needs_confirmation", False),
        }

    def _format_tool_results(self, reasoning_result: dict, tool_results: list, language: str) -> str:
        for tr in tool_results:
            if not tr.get("success"):
                continue

            if "availability" in tr:
                avail = tr["availability"]
                if avail:
                    doc = avail[0]
                    slots_str = ", ".join(doc.get("available_slots", [])[:6])
                    if language == "hi":
                        return f"डॉ. {doc.get('doctor_name', '')} {doc.get('date', '')} को उपलब्ध हैं। समय: {slots_str}। कौन सा समय चुनेंगे?"
                    elif language == "ta":
                        return f"Dr. {doc.get('doctor_name', '')} {doc.get('date', '')} அன்று கிடைக்கிறார். நேரம்: {slots_str}. எந்த நேரம் தேர்வு செய்கிறீர்கள்?"
                    else:
                        return f"Dr. {doc.get('doctor_name', '')} is available on {doc.get('date', '')}. Available slots: {slots_str}. Which time works for you?"

            if "appointments" in tr:
                apts = tr["appointments"]
                if not apts:
                    if language == "hi":
                        return "आपकी कोई आने वाली अपॉइंटमेंट नहीं है।"
                    elif language == "ta":
                        return "உங்களுக்கு வரவிருக்கும் சந்திப்புகள் இல்லை."
                    else:
                        return "You don't have any upcoming appointments."
                lines = []
                for i, a in enumerate(apts[:5], 1):
                    lines.append(f"{i}. Dr. {a.get('doctor_name', '')} - {a.get('date', '')} at {a.get('time_slot', '')}")
                return "Your upcoming appointments:\n" + "\n".join(lines)

            if "appointment_id" in tr and tr.get("status") == "scheduled":
                if language == "hi":
                    return f"अपॉइंटमेंट बुक हो गई! डॉ. {tr.get('doctor_name', '')} से {tr.get('date', '')} को {tr.get('time_slot', '')} बजे।"
                elif language == "ta":
                    return f"சந்திப்பு பதிவு செய்யப்பட்டது! Dr. {tr.get('doctor_name', '')} - {tr.get('date', '')} {tr.get('time_slot', '')}."
                else:
                    return f"Appointment booked! With Dr. {tr.get('doctor_name', '')} on {tr.get('date', '')} at {tr.get('time_slot', '')}."

        return None

    async def _background_update(self, session_id, patient_id, user_text, reasoning_result, detected_language, tool_calls, tool_results):
        try:
            actions = [
                {"tool": tc.get("tool"), "result": tr}
                for tc, tr in zip(tool_calls, tool_results)
            ] if tool_calls else []

            await memory_manager.update_after_turn(
                session_id=session_id,
                patient_id=patient_id,
                user_text=user_text,
                agent_response=reasoning_result.get("response_text", "")[:200],
                language=detected_language,
                intent=reasoning_result.get("intent"),
                actions=actions,
            )
        except Exception as e:
            logger.error("background_update_error", error=str(e))

    async def _execute_tools(self, tool_calls: list, patient_id: str) -> list:
        tasks = []
        for tc in tool_calls:
            tool_name = tc.get("tool")
            params = tc.get("parameters", {})
            tool_def = tool_registry.get(tool_name)

            if not tool_def:
                tasks.append(asyncio.coroutine(lambda: {"success": False, "error": f"Unknown tool: {tool_name}"})())
                continue

            if "patient_id" in tool_def["parameters"] and "patient_id" not in params:
                params["patient_id"] = patient_id

            tasks.append(self._execute_single_tool(tool_def, params))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [
                r if not isinstance(r, Exception) else {"success": False, "error": str(r)}
                for r in results
            ]
        return []

    async def _execute_single_tool(self, tool_def: dict, params: dict) -> dict:
        try:
            result = await asyncio.wait_for(tool_def["function"](**params), timeout=2.0)
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": "Tool timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}


agent_orchestrator = AgentOrchestrator()