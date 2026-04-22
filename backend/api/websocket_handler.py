import asyncio
import time
import uuid
import traceback
import orjson
import structlog
from fastapi import WebSocket, WebSocketDisconnect
from backend.agent.orchestrator import agent_orchestrator
from backend.services.stt_service import stt_service
from backend.services.tts_service import tts_service
from backend.services.language_detection import language_detector
from backend.services.audio_processor import audio_processor
from backend.memory.memory_manager import memory_manager
from backend.middleware.latency_tracker import latency_tracker, LatencyBreakdown

logger = structlog.get_logger()


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.session_data: dict[str, dict] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        self.session_data[session_id] = {
            "audio_buffer": [],
            "is_speaking": False,
            "patient_id": None,
            "language": "en",
            "processing": False,
            "interrupted": False,
        }

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)
        self.session_data.pop(session_id, None)
        latency_tracker.remove(session_id)

    async def send_json(self, session_id: str, data: dict):
        ws = self.active_connections.get(session_id)
        if ws:
            try:
                await ws.send_text(orjson.dumps(data).decode())
            except Exception:
                pass

    async def send_audio(self, session_id: str, audio_data: bytes):
        ws = self.active_connections.get(session_id)
        if ws:
            try:
                await ws.send_bytes(audio_data)
            except Exception:
                pass


connection_manager = ConnectionManager()


async def handle_websocket(websocket: WebSocket, session_id: str = None):
    if not session_id:
        session_id = str(uuid.uuid4())

    await connection_manager.connect(websocket, session_id)
    logger.info("ws_connected", session_id=session_id)

    await connection_manager.send_json(session_id, {
        "type": "connected",
        "session_id": session_id,
    })

    try:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning("ws_receive_error", error=str(e))
                break

            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message:
                await handle_text_message(session_id, message["text"])
            elif "bytes" in message:
                await handle_audio_message(session_id, message["bytes"])

    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=session_id)
    except Exception as e:
        logger.error("ws_error", session_id=session_id, error=str(e))
    finally:
        connection_manager.disconnect(session_id)


async def handle_text_message(session_id: str, raw: str):
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        data = {"type": "text", "content": raw}

    msg_type = data.get("type", "text")
    session_data = connection_manager.session_data.get(session_id, {})

    if msg_type == "init":
        patient_id = data.get("patient_id")
        language = data.get("language", "en")
        session_data["patient_id"] = patient_id
        session_data["language"] = language
        connection_manager.session_data[session_id] = session_data

        await memory_manager.session.update_session(session_id, {
            "patient_id": patient_id,
            "language": language,
            "collected_slots": {
                "patient_id": patient_id,
            },
        })

        if patient_id:
            try:
                profile = await memory_manager.persistent.get_patient_profile(patient_id)
                if profile.get("preferred_language"):
                    session_data["language"] = profile["preferred_language"]
                    connection_manager.session_data[session_id] = session_data
            except Exception as e:
                logger.warning("profile_load_error", error=str(e))

        await connection_manager.send_json(session_id, {
            "type": "initialized",
            "language": session_data["language"],
        })

    elif msg_type == "text":
        content = data.get("content", "")
        if content:
            asyncio.create_task(process_user_input(session_id, content))

    elif msg_type == "interrupt":
        session_data["interrupted"] = True
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {"type": "interrupted"})

    elif msg_type == "speech_end":
        asyncio.create_task(process_audio_buffer(session_id))


async def handle_audio_message(session_id: str, audio_bytes: bytes):
    session_data = connection_manager.session_data.get(session_id)
    if not session_data:
        return

    session_data["audio_buffer"].append(audio_bytes)

    if audio_processor.detect_speech_end(session_data["audio_buffer"]):
        if not session_data.get("processing"):
            session_data["processing"] = True
            connection_manager.session_data[session_id] = session_data
            asyncio.create_task(process_audio_buffer(session_id))


async def process_audio_buffer(session_id: str):
    session_data = connection_manager.session_data.get(session_id)
    if not session_data:
        return

    if not session_data["audio_buffer"]:
        logger.warning("empty_audio_buffer", session_id=session_id)
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": "No audio captured. Please try again."
        })
        return

    latency = latency_tracker.create(session_id)
    latency.speech_end_timestamp = time.perf_counter() * 1000

    audio_chunks = list(session_data["audio_buffer"])
    session_data["audio_buffer"] = []
    session_data["processing"] = True
    connection_manager.session_data[session_id] = session_data

    audio_data = b"".join(audio_chunks)

    logger.info(
        "audio_buffer_stats",
        session_id=session_id,
        chunks=len(audio_chunks),
        total_bytes=len(audio_data),
        duration_estimate=f"{len(audio_data) / (16000 * 2):.2f}s",
    )

    if len(audio_data) < 3200:
        logger.warning("audio_too_short", bytes=len(audio_data))
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": "Audio too short. Please speak longer."
        })
        return

    try:
        wav_data = audio_processor.finalize_wav(
            audio_processor.create_wav_header(),
            audio_data,
        )
        logger.info("wav_created", wav_size=len(wav_data))
    except Exception as e:
        logger.error("wav_creation_error", error=str(e), traceback=traceback.format_exc())
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": "Audio processing failed."
        })
        return

    latency.mark("stt", "start")

    try:
        stt_result = await asyncio.wait_for(
            stt_service.transcribe_audio(
                wav_data,
                language=session_data.get("language", "en")
            ),
            timeout=5.0,
        )
        logger.info("stt_result", result=stt_result)
    except asyncio.TimeoutError:
        logger.error("stt_timeout")
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": "Speech recognition timed out. Please try again."
        })
        return
    except Exception as e:
        logger.error(
            "stt_error_detailed",
            error=str(e),
            error_type=type(e).__name__,
            traceback=traceback.format_exc(),
        )
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": f"Speech recognition failed: {type(e).__name__}: {str(e) or 'unknown error'}"
        })
        return

    latency.mark("stt", "end")

    user_text = stt_result.get("text", "").strip()
    if not user_text:
        logger.warning("stt_empty_text", session_id=session_id)
        session_data["processing"] = False
        connection_manager.session_data[session_id] = session_data
        await connection_manager.send_json(session_id, {
            "type": "error",
            "message": "Could not understand audio. Please speak clearly."
        })
        return

    await connection_manager.send_json(session_id, {
        "type": "transcript",
        "text": user_text,
        "language": stt_result.get("detected_language", session_data["language"]),
    })

    await process_user_input(session_id, user_text, latency=latency)


async def process_user_input(session_id: str, user_text: str, latency: LatencyBreakdown = None):
    session_data = connection_manager.session_data.get(session_id, {})

    if not latency:
        latency = latency_tracker.create(session_id)
        latency.speech_end_timestamp = time.perf_counter() * 1000

    detected_language = language_detector.detect(user_text, fallback=session_data.get("language", "en"))
    session_data["language"] = detected_language
    connection_manager.session_data[session_id] = session_data

    patient_id = session_data.get("patient_id", session_id)

    try:
        result = await agent_orchestrator.process_turn(
            session_id=session_id,
            patient_id=patient_id,
            user_text=user_text,
            detected_language=detected_language,
            latency=latency,
        )
    except Exception as e:
        logger.error("agent_error", error=str(e), traceback=traceback.format_exc())
        result = {
            "response_text": "I'm sorry, something went wrong. Could you try again?",
            "intent": "error",
            "language": detected_language,
        }

    response_text = result.get("response_text", "")

    await connection_manager.send_json(session_id, {
        "type": "response",
        "text": response_text,
        "intent": result.get("intent"),
        "reasoning": result.get("reasoning"),
        "tool_calls": result.get("tool_calls", []),
        "tool_results": result.get("tool_results", []),
        "language": detected_language,
        "conversation_state": result.get("conversation_state"),
    })

    if response_text:
        latency.mark("tts", "start")
        try:
            first_chunk_sent = False
            async for audio_chunk, is_first in tts_service.synthesize_streaming(
                response_text, language=detected_language
            ):
                if session_data.get("interrupted"):
                    session_data["interrupted"] = False
                    connection_manager.session_data[session_id] = session_data
                    break

                if is_first:
                    latency.first_audio_response = time.perf_counter() * 1000
                    latency.mark("tts", "first_byte")
                    first_chunk_sent = True

                await connection_manager.send_audio(session_id, audio_chunk)

            latency.mark("tts", "end")
        except Exception as e:
            logger.error("tts_error", error=str(e), traceback=traceback.format_exc())
            if not first_chunk_sent:
                latency.first_audio_response = time.perf_counter() * 1000

    latency_report = latency.log()

    await connection_manager.send_json(session_id, {
        "type": "latency",
        "data": latency_report,
    })

    session_data["processing"] = False
    connection_manager.session_data[session_id] = session_data