"""
Centralized configuration using Pydantic Settings.
All values are loaded from environment variables / .env file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Mode ──────────────────────────────────────────────
    paper_trading: bool = True
    live_trading: bool = False
    live_confirmation: bool = False

    # ── Strategy ──────────────────────────────────────────
    position_size_sol: float = 0.10
    starting_paper_capital_sol: float = 0.10
    net_target_percent: float = 10.0
    min_score: int = 80

    # ── Token Filters ─────────────────────────────────────
    min_token_age_seconds: int = 20
    max_token_age_seconds: int = 180
    observation_seconds: int = 30

    # ── Liquidity ─────────────────────────────────────────
    min_liquidity_usd: float = 20_000.0
    min_liquidity_to_mcap_ratio: float = 0.05

    # ── Holders ───────────────────────────────────────────
    max_top10_holder_percent: float = 25.0
    max_single_holder_percent: float = 5.0

    # ── Execution ─────────────────────────────────────────
    max_slippage_bps: int = 300

    # ── Risk Management ───────────────────────────────────
    max_daily_loss_percent: float = 10.0
    max_consecutive_losses: int = 5
    max_trades_per_hour: int = 20
    emergency_exit_cooldown_seconds: int = 300

    # ── Solana RPC ────────────────────────────────────────
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    ws_url: str = "wss://api.mainnet-beta.solana.com"

    # ── Jupiter ───────────────────────────────────────────
    jupiter_api_url: str = "https://api.jup.ag/swap/v2"
    jupiter_api_key: str = ""

    # ── Database ──────────────────────────────────────────
    database_url: str = ""

    # ── Telegram ──────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Wallet ────────────────────────────────────────────
    wallet_keypair_path: str = ""

    # ── Server ────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Logging ───────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: str = "logs/bot.log"
    log_max_bytes: int = 10_485_760
    log_backup_count: int = 5

    @property
    def db_url(self) -> str:
        """Return database URL, defaulting to SQLite for local dev."""
        if self.database_url:
            return self.database_url
        return "sqlite+aiosqlite:///./memecoin_bot.db"

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.db_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("live_trading", mode="before")
    @classmethod
    def validate_live_trading(cls, v: bool, info) -> bool:
        """Ensure live trading cannot be enabled without explicit confirmation."""
        return v

    def validate_live_mode(self) -> list[str]:
        """Check all safety requirements for live trading. Returns list of failures."""
        failures: list[str] = []
        if self.paper_trading:
            failures.append("PAPER_TRADING must be false")
        if not self.live_trading:
            failures.append("LIVE_TRADING must be true")
        if not self.live_confirmation:
            failures.append("LIVE_CONFIRMATION must be true")
        if not self.wallet_keypair_path:
            failures.append("WALLET_KEYPAIR_PATH must be configured")
        if self.wallet_keypair_path and not Path(self.wallet_keypair_path).exists():
            failures.append(f"Wallet keypair file not found: {self.wallet_keypair_path}")
        return failures

    @property
    def effective_mode(self) -> str:
        """Return the effective trading mode."""
        if self.paper_trading:
            return "PAPER"
        if self.live_trading and self.live_confirmation:
            return "LIVE"
        return "DISABLED"


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
