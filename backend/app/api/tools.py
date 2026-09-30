"""Explicit, local-only tool calls and human decisions."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import LocalOwner, Session
from app.tools.gateway import ToolGateway

router = APIRouter(prefix="/api/conversations/{conversation_id}/tools")


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    tool_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.]*$")
    arguments: dict = Field(default_factory=dict)


class ToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool


def gateway(request: Request, session: Session) -> ToolGateway:
    return ToolGateway(session, request.app.state.tool_registry)


@router.get("")
async def catalog(conversation_id: UUID, request: Request, owner: LocalOwner, session: Session):
    return {"items": await gateway(request, session).catalog(owner, conversation_id)}


@router.get("/calls")
async def calls(conversation_id: UUID, request: Request, owner: LocalOwner, session: Session):
    return {"items": await gateway(request, session).list_calls(owner, conversation_id)}


@router.post("/calls", status_code=201)
async def invoke(conversation_id: UUID, body: ToolCallRequest, request: Request,
                 owner: LocalOwner, session: Session):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    return await gateway(request, session).invoke(
        owner, conversation_id, body.request_id, body.tool_id, body.arguments)


@router.post("/calls/{call_id}/decision")
async def decide(conversation_id: UUID, call_id: UUID, body: ToolDecision,
                 request: Request, owner: LocalOwner, session: Session):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    return await gateway(request, session).decide(owner, conversation_id, call_id,
                                                  body.approve)
