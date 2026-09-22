import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.db import init_db, seed_db
from app.service import InventoryService
from app.llm import chat as llm_chat

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY is missing. Copy .env.example to .env and set it.")

service = InventoryService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_db()
    yield

app = FastAPI(title="CURT Inventory Assistant", lifespan=lifespan)


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=500)


class ChatResponse(BaseModel):
    response: str
    tool_calls: list[dict] = []


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        result = llm_chat(req.session_id, req.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        # never leak stack traces to the client
        raise HTTPException(status_code=502, detail="Assistant is temporarily unavailable. Please try again.")
    return ChatResponse(response=result["response"], tool_calls=result.get("tool_calls", []))


@app.get("/inventory")
def inventory():
    return service.get_all_parts()


@app.get("/health")
def health():
    return {"status": "ok"}