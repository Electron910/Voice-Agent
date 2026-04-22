import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from backend.database import init_db
from backend.memory.memory_manager import memory_manager
from backend.api.websocket_handler import handle_websocket
from backend.api.rest_routes import router as rest_router
from backend.api.campaign_routes import router as campaign_router
from backend.agent.reasoning import reasoning_engine

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_application")
    await init_db()
    await memory_manager.initialize()
    await reasoning_engine.warmup()
    logger.info("application_ready")
    yield
    logger.info("shutting_down")
    await memory_manager.close()


app = FastAPI(
    title="VoiceAI Clinical Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(campaign_router)


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await handle_websocket(websocket, session_id)


@app.websocket("/ws")
async def websocket_endpoint_auto(websocket: WebSocket):
    await handle_websocket(websocket)