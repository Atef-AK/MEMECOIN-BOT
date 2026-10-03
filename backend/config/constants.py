"""
Verified Solana program IDs, well-known addresses, and system constants.
Last verified: October 2026.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════
# SOLANA PROGRAM IDS
# ═══════════════════════════════════════════════════════════════

SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
METAPLEX_TOKEN_METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"

# ═══════════════════════════════════════════════════════════════
# DEX PROGRAM IDS
# ═══════════════════════════════════════════════════════════════

RAYDIUM_AMM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAYDIUM_CPMM_PROGRAM_ID = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
RAYDIUM_CLMM_PROGRAM_ID = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
RAYDIUM_LP_LOCK_PROGRAM_ID = "LockrWmn6K5twhz3y9w1dQERbmgSaRkfnTeTKbpofwE"

ORCA_WHIRLPOOL_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"

METEORA_DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"

PUMP_FUN_BONDING_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_FUN_MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
PUMPSWAP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# ═══════════════════════════════════════════════════════════════
# WELL-KNOWN ADDRESSES
# ═══════════════════════════════════════════════════════════════

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

# Addresses commonly used as burn/dead addresses
BURN_ADDRESSES: frozenset[str] = frozenset({
    "1nc1nerator11111111111111111111111111111111",
    "1111111111111111111111111111111111111111111",
    SYSTEM_PROGRAM_ID,
})

# Common LP lock providers
LP_LOCK_PROVIDERS: dict[str, str] = {
    RAYDIUM_LP_LOCK_PROGRAM_ID: "Raydium Burn & Earn",
}

# ═══════════════════════════════════════════════════════════════
# TOKEN-2022 EXTENSION TYPE IDS
# These are the discriminator bytes for Token-2022 extensions.
# Extensions that are risky for trading are flagged.
# ═══════════════════════════════════════════════════════════════

RISKY_TOKEN2022_EXTENSIONS: frozenset[str] = frozenset({
    "TransferFeeConfig",
    "PermanentDelegate",
    "TransferHook",
    "MintCloseAuthority",
    "DefaultAccountState",
    "NonTransferable",
    "ConfidentialTransferMint",
    "ConfidentialTransferFeeConfig",
    "GroupPointer",
    "GroupMemberPointer",
    "Pausable",
})

SAFE_TOKEN2022_EXTENSIONS: frozenset[str] = frozenset({
    "MetadataPointer",
    "TokenMetadata",
    "InterestBearingConfig",  # safe to read, not risky per se
})

# ═══════════════════════════════════════════════════════════════
# SCORING WEIGHTS
# ═══════════════════════════════════════════════════════════════

SCORE_WEIGHTS = {
    "security": 30,
    "liquidity": 20,
    "holders": 20,
    "dev_history": 15,
    "social": 10,
    "market_behavior": 5,
}

SCORE_TOTAL = sum(SCORE_WEIGHTS.values())  # 100

# Score classifications
SCORE_REJECT = 70
SCORE_WATCH = 80
SCORE_TRADE = 90
SCORE_HIGH_CONFIDENCE = 100

# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"
JUPITER_BASE_URL = "https://api.jup.ag/swap/v2"
RAYDIUM_API_URL = "https://api-v3.raydium.io"

# ═══════════════════════════════════════════════════════════════
# TRADE CONSTANTS
# ═══════════════════════════════════════════════════════════════

SOL_DECIMALS = 9
LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_PRIORITY_FEE_LAMPORTS = 100_000  # 0.0001 SOL

# Solana transaction fee
BASE_TX_FEE_LAMPORTS = 5_000  # 0.000005 SOL

# Jupiter swap fee estimate (platform fee, if any)
JUPITER_PLATFORM_FEE_BPS = 0  # Jupiter itself doesn't charge; partners may

# Quote token mints we accept as base pair
ACCEPTED_QUOTE_MINTS: frozenset[str] = frozenset({
    WSOL_MINT,
    USDC_MINT,
    USDT_MINT,
})

# DEX identifiers used in discovery
DEX_IDENTIFIERS = {
    "raydium": "Raydium",
    "orca": "Orca",
    "meteora": "Meteora",
    "pump_fun": "Pump.fun",
    "pumpswap": "PumpSwap",
}
