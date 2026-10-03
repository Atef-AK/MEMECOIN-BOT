# Solana Memecoin Trading Bot - Architecture

## Overview

Production-quality system for detecting, analyzing, scoring, and paper/live-trading
newly-launched Solana memecoins. Features an **Adaptive Multi-Strategy Engine** that
tests multiple trading strategies simultaneously in paper mode, measures their real
performance, and automatically selects the strategy best suited for the current
account capital, market conditions, and historical performance.

**This is NOT a guaranteed profit system.** It collects real data and calculates expectancy.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI, asyncio |
| Database | PostgreSQL (prod) / SQLite (dev) |
| ORM | SQLAlchemy 2.0 (async) |
| Config | Pydantic Settings |
| Frontend | React 19 + Vite + TypeScript |
| Real-time | WebSocket (FastAPI → Dashboard) |
| Alerts | Telegram Bot API |
| Deploy | Docker + docker-compose |
| OS Scripts | PowerShell (Win), Bash (Linux) |

## Verified External APIs & Program IDs

### Jupiter Swap API v2 (Current - v6 is SUNSET)
- **Base URL**: `https://api.jup.ag/swap/v2`
- **Meta-Aggregator**: `/order` + `/execute` (recommended for most use cases)
- **Router**: `/build` + `/submit` (for full control)
- **Auth**: API key from Jupiter Developer Platform
- **Docs**: https://developers.jup.ag/

### DEX Screener API (Public, No Auth Required)
- **Base URL**: `https://api.dexscreener.com`
- **Endpoints**:
  - `GET /tokens/v1/{chainId}/{tokenAddress}` - Token by address
  - `GET /token-pairs/v1/{chainId}/{tokenAddress}` - Pairs for token
  - `GET /latest/dex/pairs/{chainId}/{pairAddress}` - Pair data
  - `GET /latest/dex/search?q={query}` - Search (max 30 results)
  - `GET /token-profiles/latest/v1` - Latest token profiles
  - `GET /token-boosts/latest/v1` - Latest boosted tokens
- **Rate Limits**: 300/min (pairs/tokens), 60/min (profiles/boosts)
- **Limitations**: No historical OHLC, search capped at 30

### Solana Program IDs (Verified Oct 2026)

| Program | ID |
|---------|-----|
| SPL Token | `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA` |
| Token-2022 | `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb` |
| Raydium AMM v4 | `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8` |
| Raydium CPMM | `CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C` |
| Raydium CLMM | `CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK` |
| Orca Whirlpool | `whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc` |
| Meteora DLMM | `LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo` |
| Pump.fun Bonding | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` |
| PumpSwap AMM | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` |
| Raydium LP Lock | `LockrWmn6K5twhz3y9w1dQERbmgSaRkfnTeTKbpofwE` |

### Known Addresses
| Address | Purpose |
|---------|---------|
| `So11111111111111111111111111111111111111112` | Wrapped SOL (WSOL) |
| `11111111111111111111111111111111` | System Program |

## Architecture Decisions

### 1. Adapter Pattern for External Services
All external data providers are behind abstract adapter interfaces:
- `BaseDiscoveryProvider` → DexScreener, Raydium API, on-chain RPC
- `BaseSecurityProvider` → Solana RPC (mint/account inspection)
- `BaseLiquidityProvider` → On-chain pool data, DexScreener
- `BaseExecutionProvider` → Jupiter API
- `BaseNotificationProvider` → Telegram

This allows swapping providers without changing core logic.

### 2. Paper Trading as Default
`PAPER_TRADING=true` by default. Paper mode simulates:
- Real-time executable quotes from Jupiter
- Realistic slippage/fee estimation
- Emergency exits using real market conditions
- No idealized fills

### 3. Kill Switch
Global `KILL_SWITCH` that:
- Immediately stops all new trades
- Triggers emergency exit of open positions
- Sets bot into read-only monitoring mode
- Persists across restarts (stored in DB + env)

### 4. Security-First Design
- No hardcoded keys/secrets
- Environment variables for all credentials
- Encrypted keypair support for wallet
- Private keys never logged, never sent to frontend, never stored plaintext
- Dedicated trading wallet (never primary wallet)

### 5. Conservative Default Behavior
- Unknown events → DO NOT BUY
- Unknown extensions → REJECT
- Unclassifiable state → freeze new entries, evaluate exit
- Score overrides for critical failures

## Data Flow

```
[Discovery Sources] → [Token Queue] → [Security Engine] → [Liquidity Engine]
       ↓                                      ↓                    ↓
[DEX Screener]                        [Mint Authority]      [Pool Analysis]
[Solana RPC/WS]                       [Freeze Authority]    [LP Lock Check]
[Raydium API]                         [Token-2022 Ext]      [Liquidity USD]
                                      [Supply Check]
                                            ↓
                               [Holder Engine] → [Dev Tracker]
                                      ↓                ↓
                               [Concentration]    [Wallet Graph]
                                      ↓
                             [Social Engine] → [Market Behavior]
                                      ↓                ↓
                               [Website/Social]  [Buy/Sell Ratio]
                                      ↓
                              [Scoring Engine] → [Opportunity Builder]
                                                        ↓
                                              [TokenOpportunity] ←─ [Bonding Curve Engine]
                                                        ↓            [Smart Wallet DB]
                                              [Adaptive Selector] ←─ [Market Regime Engine]
                                                   ↓    ↓            [Capital Engine]
                                              [Select] [NO TRADE]    [Fitness Engine]
                                                 ↓
                                      ┌─── FAST SCALPER
                                      ├─── MOMENTUM RUNNER
                                      └─── SMART WALLET FOLLOWER
                                                 ↓
                                      [Execution Engine] → [Monitor]
                                                 ↓              ↓
                                      [Paper/Live Trade]   [+10% NET Exit]
                                                           [Partial Exits]
                                                           [Trailing Stop]
                                                           [Emergency Exit]
                                                 ↓
                                      [Performance DB] → [Counterfactual]
                                      [Loss Protection]  [Rolling Stats]
                                                 ↓
                                          [Database] → [Dashboard]
                                                    → [Telegram]
                                                    → [Research Reports]
```

## Adaptive Multi-Strategy Engine

### Strategies

| Strategy | Purpose | Exit Model |
|----------|---------|------------|
| **Fast Scalper** | Quick +10% capture | 100% exit at target |
| **Momentum Runner** | Capture large moves | Partial exits (30/30/20%) + trailing stop |
| **Smart Wallet Follower** | Follow qualified wallets | Target exit + trailing stop |

### Strategy Selection

The selector evaluates all strategies per token using a **confidence-adjusted fitness score**:

| Weight | Component |
|--------|----------|
| 30% | Expectancy |
| 20% | Profit Factor |
| 15% | Risk-Adjusted Return |
| 15% | Max-Drawdown Quality |
| 10% | Win-Rate Quality (Wilson-bounded) |
| 5% | Execution Quality |
| 5% | Statistical Confidence |

The selector uses **UCB exploration** (10% default) to balance exploitation of the best-known
strategy with exploration of under-tested alternatives.

### Statistical Controls

- **< 50 trades**: EXPERIMENTAL status, heavy confidence penalty
- **50–199 trades**: DEVELOPING status, moderate penalty
- **200+ trades**: ESTABLISHED status, no penalty
- **Wilson intervals** for win rate prevent small-sample bias
- **Rolling windows** (20/50/100/200 trades) detect degradation
- **Counterfactual testing** evaluates all strategies on every qualifying token

### Market Regimes

`QUIET | NORMAL | HIGH_MOMENTUM | HIGH_VOLATILITY | RISK_OFF | EXTREME_SPECULATION`

Each strategy maintains separate performance statistics per regime.

### Capital Stages

| Stage | Range | Max Position | Max Slippage |
|-------|-------|--------------|--------------|
| 1 | 0.05–0.20 SOL | 100% / 0.20 SOL | 300 bps |
| 2 | 0.20–0.50 SOL | 50% / 0.25 SOL | 250 bps |
| 3 | 0.50–1.00 SOL | 30% / 0.30 SOL | 200 bps |
| 4 | 1.00–5.00 SOL | 20% / 1.00 SOL | 150 bps |
| 5 | 5.00+ SOL | 10% / 2.50 SOL | 100 bps |

## Directory Structure

```
backend/
├── __init__.py
├── main.py                    # FastAPI app entry
├── adaptive/                  # ★ Adaptive Multi-Strategy Engine
│   ├── manager.py             # Central orchestrator
│   ├── engines/               # Analysis engines
│   │   ├── bonding_curve.py   # Bonding curve state classifier
│   │   ├── market_regime.py   # Market regime classifier
│   │   ├── capital.py         # Capital-aware position sizing
│   │   ├── smart_wallet_db.py # Smart wallet tracking
│   │   └── opportunity_builder.py # TokenOpportunity constructor
│   ├── strategies/            # Independent strategy modules
│   │   ├── base.py            # Abstract base strategy
│   │   ├── fast_scalper.py    # Strategy A: +10% exit
│   │   ├── momentum_runner.py # Strategy B: partial exits + trailing
│   │   └── smart_wallet_follower.py # Strategy C: follow smart wallets
│   ├── intelligence/          # Selection & confidence
│   │   ├── confidence.py      # Wilson intervals, sample penalties
│   │   ├── fitness.py         # Composite fitness scoring
│   │   ├── selector.py        # Adaptive strategy selector (UCB)
│   │   ├── counterfactual.py  # Same-opportunity benchmarking
│   │   └── loss_protection.py # Per-strategy kill switches
│   └── performance/           # Performance tracking
│       ├── database.py        # Strategy performance DB
│       └── rolling_stats.py   # Rolling window statistics
├── api/                       # REST + WebSocket endpoints
│   ├── routes/
│   │   ├── main.py
│   │   └── adaptive.py        # ★ Strategy Intelligence API
│   └── websocket/
├── core/                      # Core business logic
│   ├── kill_switch.py
│   ├── event_bus.py
│   └── rate_limiter.py
├── config/                    # Settings & constants
│   ├── settings.py
│   └── constants.py
├── discovery/                 # Token discovery
│   ├── base.py
│   ├── dexscreener.py
│   ├── solana_rpc.py
│   └── manager.py
├── security/                  # On-chain security checks
│   ├── mint_inspector.py
│   ├── token2022.py
│   └── engine.py
├── liquidity/                 # Liquidity analysis
│   ├── pool_analyzer.py
│   ├── lock_detector.py
│   └── engine.py
├── holders/                   # Holder concentration
│   ├── analyzer.py
│   └── engine.py
├── devtracker/                # Dev wallet tracking
│   ├── wallet_graph.py
│   └── engine.py
├── social/                    # Social/website verification
│   ├── engine.py
│   └── market_observer.py
├── scoring/                   # Token scoring
│   └── engine.py
├── execution/                 # Trade execution
│   ├── jupiter.py
│   ├── paper.py
│   └── engine.py
├── monitoring/                # Position monitoring
│   ├── price_monitor.py
│   └── emergency.py
├── strategy/                  # Trading strategy
│   ├── entry.py
│   ├── exit.py
│   └── manager.py
├── database/                  # DB models & sessions
│   ├── models.py
│   ├── adaptive_models.py     # ★ Adaptive engine ORM models
│   ├── session.py
│   └── migrations/
├── notifications/             # Telegram alerts
│   ├── telegram.py
│   ├── formatter.py
│   └── strategy_alerts.py     # ★ Strategy event alerts
├── models/                    # Pydantic schemas
│   ├── token.py
│   ├── trade.py
│   ├── security.py
│   ├── score.py
│   ├── adaptive.py            # ★ Strategy/regime/capital models
│   └── opportunity.py         # ★ TokenOpportunity model
├── research/                  # ★ CLI research reports
│   ├── strategy_report.py
│   ├── compare_strategies.py
│   ├── regime_report.py
│   └── capital_scaling_report.py
├── services/                  # Service layer
│   └── bot.py
└── tests/                     # Automated tests

frontend/                      # React + Vite + TypeScript
├── src/
│   ├── components/
│   ├── pages/
│   ├── hooks/
│   ├── services/
│   └── types/
├── package.json
└── vite.config.ts

docs/                          # Documentation
scripts/                       # Startup scripts
docker/                        # Docker configs
```

