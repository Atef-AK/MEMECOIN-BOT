"""
CLI script to scan DEX platforms and seed/update qualified Smart Wallets.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from backend.adaptive.engines.smart_wallet_scanner import SmartWalletScanner
from backend.database.session import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)


async def main() -> None:
    print("==================================================")
    print("[*] Solana DEX Smart Wallet Intelligence Scanner")
    print("==================================================")
    await init_db()
    scanner = SmartWalletScanner()
    wallets = await scanner.scan_and_seed()

    print(f"\n[+] Total Qualified Smart Wallets Registered: {len(wallets)}\n")
    print(f"{'Address':<46} | {'Trades':<6} | {'Win %':<6} | {'Expectancy':<10} | {'Profit Factor':<13} | {'Score'}")
    print("-" * 105)
    for w in sorted(wallets, key=lambda x: x.smart_wallet_score, reverse=True):
        print(
            f"{w.address:<46} | {w.total_trades:<6} | {w.win_rate*100:>5.1f}% | {w.expectancy_percent:>9.1f}% | {w.profit_factor:>12.2f}x | {w.smart_wallet_score:>5.1f}"
        )
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
