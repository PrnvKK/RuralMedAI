# backend/app/main.py
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load the portable local configuration before services read environment defaults.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.services.llama_cpp_gemma_service import LlamaCppGemmaService
from app.services.llama_server_manager import LlamaServerManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
llama_server = LlamaServerManager()

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.icd_coding_service import ICDCodingService
    from app.services.procedure_coding_service import ProcedureCodingService

    await llama_server.start()
    from app.database import init_db
    logger.info("Initializing database...")
    init_db()

    def _warmup():
        logger.info("Warming up ICDCodingService...")
        ICDCodingService()
        logger.info("ICDCodingService ready.")
        logger.info("Warming up ProcedureCodingService...")
        ProcedureCodingService()
        logger.info("All clinical coding services ready.")

    asyncio.create_task(asyncio.to_thread(_warmup))
    try:
        yield
    finally:
        await llama_server.stop()


app = FastAPI(title="Parchee Edge Backend", lifespan=lifespan)

from app.api.routes import router as api_router
from app.api.ehr import router as ehr_router

app.include_router(api_router, prefix="/api")
app.include_router(ehr_router, prefix="/api/ehr")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Parchee Edge Backend is running"}


@app.websocket("/ws/live-consultation")
async def websocket_endpoint(websocket: WebSocket):
    """Route browser PCM through local Whisper ASR and Gemma form extraction."""
    await websocket.accept()
    logger.info("New WebSocket connection accepted")

    try:
        service = LlamaCppGemmaService()
        await service.handle_session(websocket)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error("Error in websocket session: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass
