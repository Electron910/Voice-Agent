import orjson
import asyncio
import re
import structlog
import google.generativeai as genai
from datetime import datetime, timedelta
from backend.config import get_settings
from backend.agent.prompts import LANGUAGE_INSTRUCTIONS

logger = structlog.get_logger()
settings = get_settings()

SPECIALIZATIONS = {
    "cardiologist": ["cardiologist", "cardio", "heart", "हृदय", "दिल", "இதய"],
    "dermatologist": ["dermatologist", "skin", "derma", "त्वचा", "தோல்"],
    "general physician": ["general physician", "gp", "general doctor", "सामान्य", "பொது"],
    "orthopedic": ["orthopedic", "ortho", "bone", "joint", "हड्डी", "எலும்பு"],
    "pediatrician": ["pediatrician", "child", "kids", "बच्चे", "குழந்தை"],
}


class ReasoningEngine:
    def __init__(self):
        self._gemini_available = False
        try:
            if settings.gemini_api_key:
                genai.configure(api_key=settings.gemini_api_key)
                self._model = genai.GenerativeModel(
                    model_name="gemini-2.0-flash-lite",
                    generation_config={
                        "temperature": 0.1,
                        "max_output_tokens": 250,
                        "response_mime_type": "application/json",
                    },
                )
                self._gemini_available = True
        except Exception as e:
            logger.warning("gemini_init_failed", error=str(e))

    async def warmup(self):
        if not self._gemini_available:
            logger.info("running_in_fallback_mode")
            return
        try:
            response = await asyncio.wait_for(
                self._model.generate_content_async('Return JSON: {"status":"ok"}'),
                timeout=15.0,
            )
            logger.info("gemini_warmed_up", response=response.text[:50])
        except Exception as e:
            logger.warning("gemini_warmup_failed_using_fallback", error=str(e))
            self._gemini_available = False

    async def reason(self, user_text: str, context: dict, is_outbound: bool = False) -> dict:
        session = context.get("session", {})
        language = session.get("language", "en")

        if self._gemini_available:
            try:
                result = await self._reason_with_gemini(user_text, context, is_outbound)
                if result:
                    return result
            except Exception as e:
                logger.warning("gemini_failed", error=str(e))

        return self._stateful_fallback(user_text, language, session)

    async def _reason_with_gemini(self, user_text: str, context: dict, is_outbound: bool) -> dict:
        session = context.get("session", {})
        language = session.get("language", "en")
        prompt = self._build_prompt(user_text, session, language, is_outbound, context)

        response = await asyncio.wait_for(
            self._model.generate_content_async(prompt),
            timeout=12.0,
        )

        content = response.text.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        parsed = orjson.loads(content)
        logger.info("gemini_response", intent=parsed.get("intent"))
        return parsed

    def _build_prompt(self, user_text: str, session: dict, language: str, is_outbound: bool, context: dict) -> str:
        state = session.get("conversation_state", "greeting")
        intent = session.get("current_intent", "none")
        slots = session.get("collected_slots", {})
        turns = session.get("turn_history", [])[-6:]
        history = "\n".join(f"{t['role']}: {t['content']}" for t in turns) if turns else ""

        return f"""Healthcare appointment assistant. Language: {language}
State: {state}. Intent: {intent}. Slots: {orjson.dumps(slots).decode()}
Conversation:
{history}
user: {user_text}

Tools: check_availability(specialization,date), book_appointment(patient_id,doctor_id,date,time_slot), cancel_appointment(appointment_id), reschedule_appointment(appointment_id,new_date,new_time_slot), list_appointments(patient_id), search_doctors(specialization)
{LANGUAGE_INSTRUCTIONS.get(language, '')}
Return JSON: {{"reasoning":"why","intent":"book|cancel|reschedule|check_availability|list|greeting|clarification|confirmation|farewell","tool_calls":[],"response_text":"reply in {language}","slots_extracted":{{}},"needs_confirmation":false,"conversation_state":"greeting|collecting_info|confirming|executing|completed"}}"""

    def _stateful_fallback(self, user_text: str, language: str, session: dict) -> dict:
        text_lower = user_text.lower().strip()
        current_state = session.get("conversation_state", "greeting")
        current_intent = session.get("current_intent", None)
        slots = dict(session.get("collected_slots", {}))

        is_yes = self._is_affirmative(text_lower)
        is_no = self._is_negative(text_lower)
        detected_intent = self._detect_intent(text_lower)
        new_slots = self._extract_slots(text_lower)

        if current_state == "confirming":
            if is_yes:
                return self._handle_confirmation_yes(language, slots, current_intent)
            elif is_no:
                slots_changed = bool(new_slots)
                slots.update(new_slots)
                if slots_changed:
                    return self._handle_booking_flow(language, slots, text_lower, "collecting_info")
                else:
                    return self._handle_confirmation_no(language, slots, current_intent)
            else:
                slots.update(new_slots)
                if new_slots:
                    return self._handle_booking_flow(language, slots, text_lower, "collecting_info")
                return self._make_response(
                    language, current_intent or "book", "confirming", slots,
                    {
                        "en": "Please say 'yes' to confirm or 'no' to make changes. You can also tell me what you'd like to change.",
                        "hi": "कृपया 'हाँ' बोलें पुष्टि के लिए या 'नहीं' बदलने के लिए। आप बता सकते हैं क्या बदलना है।",
                        "ta": "உறுதி செய்ய 'ஆம்' அல்லது மாற்ற 'இல்லை' என சொல்லுங்கள்.",
                    }
                )

        slots.update(new_slots)

        if detected_intent and detected_intent not in ("clarification", "confirmation"):
            current_intent = detected_intent
        elif current_intent and not detected_intent:
            detected_intent = current_intent
        elif not current_intent and not detected_intent:
            detected_intent = "clarification"

        if not detected_intent:
            detected_intent = current_intent or "clarification"

        if detected_intent == "greeting":
            return self._make_response(
                language, "greeting", "greeting", {},
                {
                    "en": "Hello! I can help you book, reschedule, or cancel appointments. What would you like to do?",
                    "hi": "नमस्ते! मैं अपॉइंटमेंट बुक, रीशेड्यूल या कैंसल करने में मदद कर सकता हूं।",
                    "ta": "வணக்கம்! சந்திப்பை பதிவு, மாற்ற அல்லது ரத்து செய்ய உதவுவேன்.",
                }
            )

        if detected_intent == "farewell":
            return self._make_response(
                language, "farewell", "completed", {},
                {
                    "en": "Thank you! Have a great day. Call back anytime.",
                    "hi": "धन्यवाद! आपका दिन शुभ हो।",
                    "ta": "நன்றி! நல்ல நாள் வாழ்த்துகள்.",
                }
            )

        if detected_intent == "list":
            return self._make_response(
                language, "list", "executing", slots,
                {
                    "en": "Let me check your upcoming appointments.",
                    "hi": "आपकी अपॉइंटमेंट देखता हूं।",
                    "ta": "உங்கள் சந்திப்புகளை பார்க்கிறேன்.",
                },
                tool_calls=[{"tool": "list_appointments", "parameters": {}}],
            )

        if detected_intent == "book":
            return self._handle_booking_flow(language, slots, text_lower, current_state)

        if detected_intent == "cancel":
            return self._make_response(
                language, "cancel", "collecting_info", slots,
                {
                    "en": "Let me show your appointments so you can tell me which one to cancel.",
                    "hi": "आपकी अपॉइंटमेंट दिखाता हूं, बताएं कौन सी रद्द करनी है।",
                    "ta": "உங்கள் சந்திப்புகளை காட்டுகிறேன், எதை ரத்து செய்ய வேண்டும் என சொல்லுங்கள்.",
                },
                tool_calls=[{"tool": "list_appointments", "parameters": {}}],
            )

        if detected_intent == "reschedule":
            return self._make_response(
                language, "reschedule", "collecting_info", slots,
                {
                    "en": "Let me show your appointments so you can tell me which one to reschedule.",
                    "hi": "आपकी अपॉइंटमेंट दिखाता हूं, बताएं कौन सी बदलनी है।",
                    "ta": "உங்கள் சந்திப்புகளை காட்டுகிறேன், எதை மாற்ற வேண்டும் என சொல்லுங்கள்.",
                },
                tool_calls=[{"tool": "list_appointments", "parameters": {}}],
            )

        if current_intent == "book":
            return self._handle_booking_flow(language, slots, text_lower, current_state)

        return self._make_response(
            language, "clarification", "greeting", slots,
            {
                "en": "I can help with booking, rescheduling, or cancelling appointments. What would you like?",
                "hi": "मैं अपॉइंटमेंट बुक, रीशेड्यूल या कैंसल कर सकता हूं। क्या करना चाहेंगे?",
                "ta": "சந்திப்பை பதிவு, மாற்ற அல்லது ரத்து செய்ய உதவ முடியும். என்ன செய்ய வேண்டும்?",
            }
        )

    def _handle_booking_flow(self, language: str, slots: dict, text_lower: str, current_state: str) -> dict:
        specialization = slots.get("specialization")
        date = slots.get("date")
        time_slot = slots.get("time_slot")

        if not specialization:
            return self._make_response(
                language, "book", "collecting_info", slots,
                {
                    "en": "What type of doctor do you need? Options: cardiologist, dermatologist, general physician, orthopedic, pediatrician.",
                    "hi": "किस प्रकार के डॉक्टर चाहिए? विकल्प: हृदय रोग, त्वचा, सामान्य चिकित्सक, हड्डी, बाल रोग।",
                    "ta": "எந்த வகை மருத்துவர்? இதய, தோல், பொது, எலும்பு, குழந்தை நிபுணர்.",
                }
            )

        if not date:
            return self._make_response(
                language, "book", "collecting_info", slots,
                {
                    "en": f"When would you like to see the {specialization}? Say 'tomorrow', 'day after tomorrow', or a specific date.",
                    "hi": f"{specialization} से कब मिलना चाहेंगे? कल, परसों, या कोई तारीख बताएं।",
                    "ta": f"{specialization} எப்போது பார்க்க வேண்டும்? நாளை, நாளை மறுநாள், அல்லது தேதி சொல்லுங்கள்.",
                }
            )

        if not time_slot:
            return self._make_response(
                language, "book", "collecting_info", slots,
                {
                    "en": f"Checking {specialization} availability on {date}...",
                    "hi": f"{date} को {specialization} की उपलब्धता देखता हूं...",
                    "ta": f"{date} அன்று {specialization} கிடைக்கும் நேரம் பார்க்கிறேன்...",
                },
                tool_calls=[{"tool": "check_availability", "parameters": {"specialization": specialization, "date": date}}],
            )

        return self._make_response(
            language, "book", "confirming", slots,
            {
                "en": f"Ready to book: {specialization} on {date} at {time_slot}. Shall I confirm? (yes/no)",
                "hi": f"बुक करें: {specialization}, {date} को {time_slot} बजे। पुष्टि करें? (हाँ/नहीं)",
                "ta": f"பதிவு: {specialization}, {date} அன்று {time_slot}. உறுதி செய்யவா? (ஆம்/இல்லை)",
            }
        )

    def _handle_confirmation_yes(self, language: str, slots: dict, current_intent: str) -> dict:
        if current_intent == "book" and slots.get("specialization") and slots.get("date") and slots.get("time_slot"):
            return self._make_response(
                language, "book", "executing", slots,
                {
                    "en": f"Booking your appointment...",
                    "hi": f"अपॉइंटमेंट बुक कर रहा हूं...",
                    "ta": f"சந்திப்பை பதிவு செய்கிறேன்...",
                },
                tool_calls=[{
                    "tool": "book_appointment",
                    "parameters": {
                        "doctor_id": slots.get("doctor_id", ""),
                        "date": slots["date"],
                        "time_slot": slots["time_slot"],
                    }
                }],
            )

        return self._make_response(
            language, current_intent or "clarification", "collecting_info", slots,
            {
                "en": "I'm missing some details. Let me help you complete the booking.",
                "hi": "कुछ जानकारी कम है। बुकिंग पूरी करने में मदद करता हूं।",
                "ta": "சில விவரங்கள் இல்லை. பதிவை முடிக்க உதவுகிறேன்.",
            }
        )

    def _handle_confirmation_no(self, language: str, slots: dict, current_intent: str) -> dict:
        return self._make_response(
            language, current_intent or "book", "collecting_info", slots,
            {
                "en": "No problem! What would you like to change? You can change the doctor, date, or time.",
                "hi": "कोई बात नहीं! क्या बदलना चाहेंगे? डॉक्टर, तारीख, या समय बदल सकते हैं।",
                "ta": "பரவாயில்லை! என்ன மாற்ற வேண்டும்? மருத்துவர், தேதி, அல்லது நேரம் மாற்றலாம்.",
            }
        )

    def _is_affirmative(self, text: str) -> bool:
        positives = [
            "yes", "yeah", "yep", "sure", "confirm", "ok", "okay",
            "go ahead", "please do", "book it", "do it", "correct",
            "हाँ", "हां", "जी", "ठीक", "करो", "बुक करो", "हा",
            "ஆம்", "சரி", "செய்", "பதிவு செய்",
        ]
        return any(p in text for p in positives)

    def _is_negative(self, text: str) -> bool:
        negatives = [
            "no", "nope", "don't", "dont", "cancel", "stop", "wait",
            "change", "not", "wrong", "different",
            "नहीं", "मत", "रुको", "बदलो", "गलत",
            "இல்லை", "வேண்டாம்", "நிறுத்து",
        ]
        return any(n in text for n in negatives)

    def _detect_intent(self, text_lower: str) -> str:
        intent_keywords = {
            "greeting": ["hello", "hi ", "hey", "good morning", "good afternoon", "good evening",
                         "नमस्ते", "हेलो", "வணக்கம்"],
            "farewell": ["bye", "goodbye", "thank you", "thanks", "see you",
                         "धन्यवाद", "अलविदा", "நன்றி", "போறேன்"],
            "book": ["book", "appointment", "schedule", "want to see", "need to see",
                     "need a doctor", "want a doctor", "new appointment",
                     "बुक", "अपॉइंटमेंट", "मिलना",
                     "பதிவு", "சந்திப்பு", "பார்க்க"],
            "cancel": ["cancel my", "cancel the", "cancel appointment", "remove appointment",
                       "अपॉइंटमेंट रद्द", "कैंसल",
                       "சந்திப்பு ரத்து"],
            "reschedule": ["reschedule", "change time", "change date", "move my", "postpone",
                           "रीशेड्यूल", "समय बदलो",
                           "மாற்று", "நேரம் மாற்ற"],
            "list": ["list", "show my", "my appointment", "upcoming", "what appointment",
                     "मेरी अपॉइंटमेंट दिखाओ",
                     "என் சந்திப்பு காட்டு"],
        }

        for intent, keywords in intent_keywords.items():
            for kw in keywords:
                if kw in text_lower:
                    return intent
        return None

    def _extract_slots(self, text_lower: str) -> dict:
        slots = {}

        for spec, keywords in SPECIALIZATIONS.items():
            for kw in keywords:
                if kw in text_lower:
                    slots["specialization"] = spec
                    break
            if "specialization" in slots:
                break

        today = datetime.utcnow().date()
        if "tomorrow" in text_lower or "कल" in text_lower or "நாளை" in text_lower:
            if "day after" in text_lower or "परसों" in text_lower or "நாளை மறுநாள்" in text_lower:
                slots["date"] = str(today + timedelta(days=2))
            else:
                slots["date"] = str(today + timedelta(days=1))
        elif "today" in text_lower or "आज" in text_lower or "இன்று" in text_lower:
            slots["date"] = str(today)
        elif "next week" in text_lower or "अगले हफ्ते" in text_lower:
            slots["date"] = str(today + timedelta(days=7))

        time_patterns = [
            (r'(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)', 3),
            (r'(\d{1,2})\s*(am|pm)', 2),
            (r'at\s+(\d{1,2})\s*(am|pm)', 2),
            (r'(\d{1,2})\s*(?:o\'?clock)', 1),
            (r'at\s+(\d{1,2})', 1),
        ]

        for pattern, groups in time_patterns:
            match = re.search(pattern, text_lower)
            if match:
                g = match.groups()
                hour = int(g[0])
                minute = "00"

                if groups >= 3:
                    minute = g[1]
                    period = g[2].lower()
                    if period == "pm" and hour != 12:
                        hour += 12
                    if period == "am" and hour == 12:
                        hour = 0
                elif groups == 2:
                    period = g[1].lower()
                    if period == "pm" and hour != 12:
                        hour += 12
                    if period == "am" and hour == 12:
                        hour = 0
                else:
                    if 1 <= hour <= 7:
                        hour += 12

                slots["time_slot"] = f"{hour:02d}:{minute}"
                break

        return slots

    def _make_response(self, language: str, intent: str, state: str, slots: dict, messages: dict, tool_calls: list = None) -> dict:
        return {
            "reasoning": f"State: {state}, Intent: {intent}, Slots: {slots}",
            "intent": intent,
            "tool_calls": tool_calls or [],
            "response_text": messages.get(language, messages.get("en", "")),
            "slots_extracted": slots,
            "needs_confirmation": state == "confirming",
            "conversation_state": state,
        }


reasoning_engine = ReasoningEngine()