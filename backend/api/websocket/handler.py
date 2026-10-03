"""
WebSocket endpoint for real-time dashboard updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

from collections import deque

# In-memory ring buffer for recent logs
_log_buffer: deque = deque(maxlen=300)


class WebSocketLogHandler(logging.Handler):
    """Logging handler that pipes log records into the log buffer and broadcasts via WebSocket."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Skip high-frequency internal websocket debug
            if record.name.startswith("backend.api.websocket"):
                return

            entry = {
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "level": record.levelname,
                "module": record.name.split(".")[-1],
                "message": record.getMessage(),
            }
            _log_buffer.append(entry)

            # Non-blocking broadcast if an event loop is running
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running() and _clients:
                    loop.create_task(broadcast("log_message", entry))
            except RuntimeError:
                pass
        except Exception:
            pass


from pathlib import Path

def get_recent_logs() -> list[dict[str, Any]]:
    """Retrieve snapshot of recent logs, parsing from logs/bot.log as fallback."""
    if len(_log_buffer) >= 20:
        return list(_log_buffer)

    entries = list(_log_buffer)
    log_file = Path("logs/bot.log")
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-80:]
                file_entries = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            d = json.loads(line)
                            t_str = d.get("time", "")
                            file_entries.append({
                                "time": t_str.split(" ")[-1] if " " in t_str else t_str,
                                "level": d.get("level", "INFO"),
                                "module": d.get("module", "bot"),
                                "message": d.get("message", ""),
                            })
                        except Exception:
                            pass
                    elif " | " in line:
                        parts = line.split(" | ", 3)
                        if len(parts) >= 4:
                            file_entries.append({
                                "time": parts[0].split(" ")[-1],
                                "level": parts[1].strip(),
                                "module": parts[2].strip().split(".")[-1],
                                "message": parts[3].strip(),
                            })
                if file_entries:
                    return file_entries
        except Exception:
            pass
    return entries



ws_router = APIRouter()

# Connected clients
_clients: set[WebSocket] = set()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    _clients.add(websocket)
    logger.info(f"WebSocket client connected ({len(_clients)} total)")

    try:
        while True:
            # Keep connection alive, handle incoming messages
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                # Handle ping/pong or client messages
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
    finally:
        _clients.discard(websocket)
        logger.info(f"WebSocket client disconnected ({len(_clients)} total)")


async def broadcast(event_type: str, data: dict[str, Any]) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    if not _clients:
        return

    message = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, default=str)

    disconnected = set()
    for client in _clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.add(client)

    _clients.difference_update(disconnected)


async def broadcast_token_discovered(token_data: dict) -> None:
    await broadcast("token_discovered", token_data)


async def broadcast_trade_update(trade_data: dict) -> None:
    await broadcast("trade_update", trade_data)


async def broadcast_position_update(position_data: dict) -> None:
    await broadcast("position_update", position_data)


async def broadcast_stats_update(stats_data: dict) -> None:
    await broadcast("stats_update", stats_data)

