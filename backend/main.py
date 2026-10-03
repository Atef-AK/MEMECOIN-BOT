"""
FastAPI application entry point.
Serves REST API, WebSocket, and manages bot lifecycle.
"""

from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Fix Windows console UTF-8 encoding for emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.adaptive.manager import AdaptiveManager
from backend.api.routes.adaptive import router as adaptive_router
from backend.api.routes.main import router as api_router, set_strategy_manager
from backend.api.websocket.handler import ws_router, WebSocketLogHandler
from backend.config.settings import get_settings
from backend.database.session import init_db, close_db
from backend.discovery.dexscreener import DexScreenerProvider
from backend.discovery.pump_fun import PumpFunDiscoveryProvider
from backend.discovery.solana_rpc import SolanaRpcProvider
from backend.strategy.manager import StrategyManager


class SafeStreamHandler(logging.StreamHandler):
    """Console stream handler that safely writes UTF-8 on Windows without charmap encoding errors."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if hasattr(self.stream, "buffer"):
                self.stream.buffer.write((msg + self.terminator).encode("utf-8", errors="replace"))
                self.stream.buffer.flush()
            else:
                self.stream.write(msg + self.terminator)
                self.flush()
        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Configure structured logging with rotation and WebSocket streaming."""
    settings = get_settings()

    # Create logs directory
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root_logger.handlers.clear()

    # Safe Console handler
    console = SafeStreamHandler(sys.stdout)
    console.setLevel(logging.INFO)

    if settings.log_format == "json":
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"module":"%(module)s","message":"%(message)s"}'
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )

    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # WebSocket real-time broadcast handler
    ws_handler = WebSocketLogHandler()
    ws_handler.setLevel(logging.INFO)
    root_logger.addHandler(ws_handler)

    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)



# Strategy manager (module-level for access from routes)
strategy_manager: StrategyManager | None = None
adaptive_manager: AdaptiveManager | None = None
bot_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    global strategy_manager, adaptive_manager, bot_task

    setup_logging()
    logger = logging.getLogger(__name__)

    settings = get_settings()
    logger.info(f"Starting Solana Memecoin Bot in {settings.effective_mode} mode")

    # Initialize database
    await init_db()

    # Create and start adaptive strategy engine
    adaptive_manager = AdaptiveManager()
    await adaptive_manager.start()
    app.state.adaptive_manager = adaptive_manager

    # Print startup banner
    balance = getattr(settings, 'initial_balance_sol', 0.10)
    banner = adaptive_manager.get_startup_banner(balance)
    logger.info(f"\n{banner}")

    # Create and configure strategy manager with adaptive engine
    strategy_manager = StrategyManager(adaptive=adaptive_manager)

    # Register discovery providers
    strategy_manager.discovery.register_provider(PumpFunDiscoveryProvider())
    strategy_manager.discovery.register_provider(DexScreenerProvider())
    strategy_manager.discovery.register_provider(SolanaRpcProvider())

    # Set strategy manager reference for API routes
    set_strategy_manager(strategy_manager)

    # Start strategy
    await strategy_manager.start()

    # Launch bot in background
    bot_task = asyncio.create_task(strategy_manager.run())

    logger.info("Bot started successfully")

    yield

    # Shutdown
    logger.info("Shutting down...")
    if bot_task and not bot_task.done():
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

    if strategy_manager:
        await strategy_manager.stop()

    if adaptive_manager:
        await adaptive_manager.stop()

    await close_db()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Solana Memecoin Trading Bot",
        description="Production memecoin trading bot with paper/live mode",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(api_router)
    app.include_router(adaptive_router)
    app.include_router(ws_router)

    # Placeholder for adaptive manager (set during lifespan)
    app.state.adaptive_manager = None

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level="info",
        log_config=None,
    )
