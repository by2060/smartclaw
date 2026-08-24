"""
Global routes for SmartClaw TUI compatibility

Provides /global/* endpoints that SmartClaw SDK expects.

SmartClaw expects health response:
{
    "healthy": true,
    "version": "x.x.x"
}
"""

import asyncio
import json
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from smartclaw.utils.log import Log
from smartclaw.server.routes.event import EventBroadcaster, sse_generator


router = APIRouter()
log = Log.create(service="global-routes")


class HealthResponse(BaseModel):
    healthy: Literal[True] = True
    version: str = "unknown"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Get health",
    description="Get health information about the SmartClaw server"
)
async def get_health() -> HealthResponse:
    """Health check endpoint for SmartClaw TUI"""
    from smartclaw.updater import get_current_version
    return HealthResponse(version=get_current_version())


@router.get(
    "/event",
    summary="Get global events",
    description="Subscribe to global events using server-sent events"
)
async def get_global_events(request: Request):
    """
    Subscribe to global SSE event stream
    
    This is the main event endpoint that SmartClaw TUI uses.
    """
    queue = await EventBroadcaster.get().subscribe()
    
    log.info("global.event.subscribe", {
        "clients": EventBroadcaster.get().client_count,
    })
    
    return StreamingResponse(
        sse_generator(queue, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post(
    "/dispose",
    summary="Dispose instance",
    description="Clean up and dispose all SmartClaw instances"
)
async def dispose_global():
    """Dispose all instances"""
    log.info("global.dispose")
    return {"success": True}
