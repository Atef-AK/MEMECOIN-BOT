"""
CLI Tool to Import Smart Wallets from Dune, GMGN, Birdeye, or Cielo CSV Exports.
Applies the 5 Critical Filters:
1. Anti-insider / anti-bundler validation
2. Realized PnL only
3. Minimum 30 trades (anti-survivorship bias)
4. Win rate >= 55%
5. Valid Solana EOA verification (no program IDs or mints)
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from backend.adaptive.engines.smart_wallet_pipeline import SmartWalletPipeline
from backend.database.session import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)


async def main() -> None:
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "data/smart_wallets_template.csv"
    csv_path = Path(csv_file)

    print("==================================================")
    print("📥 Smart Wallet CSV Import & Filter Engine")
    print(f"📄 Source: {csv_path.resolve()}")
    print("==================================================")

    if not csv_path.exists():
        print(f"[!] Error: File {csv_file} does not exist.")
        return

    await init_db()
    pipeline = SmartWalletPipeline()
    wallets = await pipeline.import_from_csv(csv_path)
    await pipeline.close()

    print(f"\n[+] Successfully Validated & Stored: {len(wallets)} Smart Wallets\n")
    print(f"{'Address':<46} | {'Trades':<6} | {'Win %':<6} | {'Expectancy':<10} | {'Profit Factor':<13} | {'Score'}")
    print("-" * 105)
    for w in sorted(wallets, key=lambda x: x.smart_wallet_score, reverse=True):
        print(
            f"{w.address:<46} | {w.total_trades:<6} | {w.win_rate*100:>5.1f}% | {w.expectancy_percent:>9.1f}% | {w.profit_factor:>12.2f}x | {w.smart_wallet_score:>5.1f}"
        )
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
