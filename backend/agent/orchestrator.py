import asyncio
import uuid as uuid_mod
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
            if "patient_id" not in extracted_slots:
                extracted_slots["patient_id"] = patient_id
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
                    self._execute_tools(tool_calls, patient_id, session_id),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                tool_results = [{"success": False, "error": "Tool timed out"}]
            if latency:
                latency.mark("tool_exec", "end")

            await self._process_tool_results(session_id, tool_calls, tool_results)

            if tool_results:
                tool_response = self._format_tool_results(
                    reasoning_result, tool_results, detected_language
                )
                if tool_response:
                    reasoning_result["response_text"] = tool_response

        await memory_manager.session.append_turn(session_id, "user", user_text)
        await memory_manager.session.append_turn(
            session_id, "assistant", reasoning_result.get("response_text", "")
        )

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

    async def _process_tool_results(
        self, session_id: str, tool_calls: list, tool_results: list
    ):
        session = await memory_manager.session.get_session(session_id)
        if not session:
            session = {}
        slots = dict(session.get("collected_slots", {}))
        updated = False

        for i, tool_result in enumerate(tool_results):
            if not isinstance(tool_result, dict):
                continue

            tool_name = tool_calls[i].get("tool", "") if i < len(tool_calls) else ""

            if not tool_result.get("success"):
                error = tool_result.get("error", "")
                if tool_name == "book_appointment" and (
                    "UUID" in error or "doctor" in error.lower()
                ):
                    slots.pop("doctor_id", None)
                    slots.pop("doctor_name", None)
                    updated = True
                    logger.warning("cleared_bad_doctor_id", error=error)
                continue

            if tool_name == "search_doctors":
                doctors = (
                    tool_result.get("doctors", [])
                    or tool_result.get("data", {}).get("doctors", [])
                    or tool_result.get("results", [])
                )
                if doctors:
                    doctor = doctors[0]
                    doctor_id = str(
                        doctor.get("id",
                        doctor.get("doctor_id",
                        doctor.get("uuid", "")))
                    )
                    doctor_name = (
                        doctor.get("name", "")
                        or doctor.get("doctor_name", "")
                    )
                    if doctor_id:
                        slots["doctor_id"] = doctor_id
                        slots["doctor_name"] = doctor_name
                        updated = True
                        logger.info(
                            "doctor_id_extracted",
                            doctor_id=doctor_id,
                            doctor_name=doctor_name,
                            total_found=len(doctors),
                        )

            elif tool_name == "check_availability":
                avail = tool_result.get("availability", [])
                if avail and isinstance(avail, list):
                    for doc_avail in avail:
                        doc_id = str(
                            doc_avail.get("doctor_id",
                            doc_avail.get("id", ""))
                        )
                        doc_name = doc_avail.get("doctor_name", "")

                        if doc_id and not slots.get("doctor_id"):
                            slots["doctor_id"] = doc_id
                            updated = True

                        if doc_name and not slots.get("doctor_name"):
                            slots["doctor_name"] = doc_name
                            updated = True

                        available_slot_times = doc_avail.get("available_slots", [])
                        if available_slot_times:
                            slots["available_slots"] = available_slot_times
                            updated = True
                            logger.info(
                                "available_slots_stored",
                                slots=available_slot_times,
                            )

                    session["available_slots"] = avail
                    updated = True

            elif tool_name == "book_appointment":
                appt_id = (
                    tool_result.get("appointment_id")
                    or tool_result.get("id")
                )
                if appt_id:
                    slots["appointment_id"] = str(appt_id)
                    updated = True
                    logger.info("appointment_booked", appointment_id=appt_id)

            elif tool_name == "list_appointments":
                appointments = tool_result.get("appointments", [])
                if appointments:
                    session["patient_appointments"] = appointments
                    updated = True

        if updated:
            session["collected_slots"] = slots
            await memory_manager.session.update_session(session_id, {
                "collected_slots": slots,
            })
            logger.info(
                "session_slots_updated_from_tools",
                session_id=session_id,
                doctor_id=slots.get("doctor_id"),
                doctor_name=slots.get("doctor_name"),
                patient_id=slots.get("patient_id"),
                all_slots=slots,
            )

    def _format_doctor_name(self, name: str) -> str:
        if not name:
            return ""
        clean = name.strip()
        if clean.lower().startswith("dr."):
            clean = clean[3:].strip()
        elif clean.lower().startswith("dr "):
            clean = clean[3:].strip()
        return f"Dr. {clean}"

    def _format_tool_results(
        self, reasoning_result: dict, tool_results: list, language: str
    ) -> str:
        for tr in tool_results:
            if not tr.get("success"):
                continue

            if "doctors" in tr or "results" in tr:
                doctors = tr.get("doctors", tr.get("results", []))
                if doctors:
                    doc = doctors[0]
                    raw_name = doc.get("name", doc.get("doctor_name", ""))
                    name = self._format_doctor_name(raw_name)
                    if len(doctors) == 1:
                        if language == "hi":
                            return f"{name} उपलब्ध हैं। आप किस दिन मिलना चाहेंगे?"
                        elif language == "ta":
                            return f"{name} கிடைக்கிறார். எந்த நாள் சந்திக்க விரும்புகிறீர்கள்?"
                        else:
                            return f"Found {name}. What date would you like to schedule?"
                    else:
                        names = ", ".join(
                            self._format_doctor_name(
                                d.get("name", d.get("doctor_name", ""))
                            )
                            for d in doctors[:5]
                        )
                        if language == "hi":
                            return f"उपलब्ध डॉक्टर: {names}। पहले डॉक्टर को चुनता हूं। किस दिन मिलना चाहेंगे?"
                        elif language == "ta":
                            return f"கிடைக்கும் மருத்துவர்கள்: {names}. எந்த நாள் சந்திக்க விரும்புகிறீர்கள்?"
                        else:
                            return f"Available doctors: {names}. I'll go with the first one. What date works for you?"

            if "availability" in tr:
                avail = tr["availability"]
                if avail:
                    doc = avail[0]
                    raw_name = doc.get("doctor_name", "")
                    name = self._format_doctor_name(raw_name)
                    slots_str = ", ".join(doc.get("available_slots", [])[:6])
                    date = doc.get("date", "")
                    if language == "hi":
                        return f"{name} {date} को उपलब्ध हैं। समय: {slots_str}। कौन सा समय चुनेंगे?"
                    elif language == "ta":
                        return f"{name} {date} அன்று கிடைக்கிறார். நேரம்: {slots_str}. எந்த நேரம்?"
                    else:
                        return f"{name} is available on {date}. Slots: {slots_str}. Which time works?"

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
                    name = self._format_doctor_name(a.get("doctor_name", ""))
                    lines.append(
                        f"{i}. {name} - "
                        f"{a.get('date', '')} at {a.get('time_slot', '')}"
                    )
                return "Your appointments:\n" + "\n".join(lines)

            if "appointment_id" in tr and tr.get("status") == "scheduled":
                raw_name = tr.get("doctor_name", "")
                name = self._format_doctor_name(raw_name)
                date = tr.get("date", "")
                time_slot = tr.get("time_slot", "")
                if language == "hi":
                    return f"अपॉइंटमेंट बुक हो गई! {name} से {date} को {time_slot} बजे।"
                elif language == "ta":
                    return f"சந்திப்பு பதிவு! {name} - {date} {time_slot}."
                else:
                    return f"Appointment booked! With {name} on {date} at {time_slot}."

        return None

    async def _execute_tools(
        self, tool_calls: list, patient_id: str, session_id: str
    ) -> list:
        session = await memory_manager.session.get_session(session_id)
        current_slots = {}
        if session:
            current_slots = session.get("collected_slots", {})

        tasks = []
        for tc in tool_calls:
            tool_name = tc.get("tool")
            params = dict(tc.get("parameters", {}))
            tool_def = tool_registry.get(tool_name)

            if not tool_def:
                async def _unknown(name=tool_name):
                    return {"success": False, "error": f"Unknown tool: {name}"}
                tasks.append(_unknown())
                continue

            if "patient_id" in tool_def["parameters"]:
                if not params.get("patient_id") or params["patient_id"] == "":
                    params["patient_id"] = patient_id
                    logger.info(
                        "injected_patient_id",
                        tool=tool_name,
                        patient_id=patient_id,
                    )

            if "doctor_id" in tool_def.get("parameters", {}):
                if not params.get("doctor_id") and current_slots.get("doctor_id"):
                    params["doctor_id"] = current_slots["doctor_id"]
                    logger.info(
                        "injected_doctor_id_from_session",
                        tool=tool_name,
                        doctor_id=current_slots["doctor_id"],
                    )

            if tool_name == "book_appointment":
                doctor_id = params.get("doctor_id", "")
                p_id = params.get("patient_id", "")

                if not doctor_id:
                    async def _no_doctor():
                        return {
                            "success": False,
                            "error": "No doctor selected. Please search for a doctor first.",
                        }
                    tasks.append(_no_doctor())
                    logger.warning("blocked_booking_no_doctor_id", params=params)
                    continue

                if not p_id:
                    async def _no_patient():
                        return {
                            "success": False,
                            "error": "No patient ID available.",
                        }
                    tasks.append(_no_patient())
                    logger.warning("blocked_booking_no_patient_id", params=params)
                    continue

                try:
                    uuid_mod.UUID(doctor_id)
                except ValueError:
                    async def _bad_doctor(did=doctor_id):
                        return {
                            "success": False,
                            "error": f"Invalid doctor_id format: {did}",
                        }
                    tasks.append(_bad_doctor())
                    logger.warning("blocked_booking_bad_doctor_uuid", doctor_id=doctor_id)
                    continue

                try:
                    uuid_mod.UUID(p_id)
                except ValueError:
                    async def _bad_patient(pid=p_id):
                        return {
                            "success": False,
                            "error": f"Invalid patient_id format: {pid}",
                        }
                    tasks.append(_bad_patient())
                    logger.warning("blocked_booking_bad_patient_uuid", patient_id=p_id)
                    continue

            tasks.append(self._execute_single_tool(tool_def, params))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [
                r if not isinstance(r, Exception)
                else {"success": False, "error": str(r)}
                for r in results
            ]
        return []

    async def _execute_single_tool(self, tool_def: dict, params: dict) -> dict:
        try:
            result = await asyncio.wait_for(
                tool_def["function"](**params), timeout=2.0
            )
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": "Tool timed out"}
        except Exception as e:
            logger.error("tool_execution_error", error=str(e), params=params)
            return {"success": False, "error": str(e)}

    async def _background_update(
        self, session_id, patient_id, user_text,
        reasoning_result, detected_language, tool_calls, tool_results
    ):
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


agent_orchestrator = AgentOrchestrator()