from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    provider: Optional[Literal["openai", "claude"]] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider_used: str
    memory_size: int
