"""
Social/website verification engine.
Verifies the existence and quality of token social presence.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from backend.models.security import SocialReport

logger = logging.getLogger(__name__)


class SocialEngine:
    """
    Verifies token social presence and website availability.

    IMPORTANT: Social presence is scored as a signal but NEVER
    overrides on-chain security failures.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SolanaBot/1.0)",
            },
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(
        self,
        mint_address: str,
        website: str = "",
        twitter: str = "",
        telegram: str = "",
        discord: str = "",
    ) -> SocialReport:
        """Analyze social presence for a token."""
        report = SocialReport(mint_address=mint_address)

        # Website check
        if website:
            report.has_website = True
            report.website_url = website
            report.website_https = website.lower().startswith("https://")
            report.website_responds = await self._check_url(website)

        # Twitter check
        if twitter:
            report.has_twitter = True
            report.twitter_url = twitter

        # Telegram check
        if telegram:
            report.has_telegram = True
            report.telegram_url = telegram

        # Discord check
        if discord:
            report.has_discord = True
            report.discord_url = discord

        # Count social presence
        report.social_count = sum([
            report.has_website,
            report.has_twitter,
            report.has_telegram,
            report.has_discord,
        ])

        # Calculate score
        self._calculate_score(report)
        report.passed = True  # Social is never a hard rejection by itself
        return report

    async def _check_url(self, url: str) -> bool:
        """Check if a URL responds with 2xx status."""
        if not self._client or not url:
            return False

        try:
            # Validate URL format
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False

            response = await self._client.head(url)
            return 200 <= response.status_code < 400
        except Exception:
            try:
                # Retry with GET if HEAD fails
                response = await self._client.get(url)
                return 200 <= response.status_code < 400
            except Exception:
                return False

    def _calculate_score(self, report: SocialReport) -> None:
        """Calculate social score out of 10 points."""
        score = 0.0

        # Website (0-4 points)
        if report.has_website:
            score += 2.0
            if report.website_responds:
                score += 1.0
            if report.website_https:
                score += 1.0

        # Twitter (0-3 points)
        if report.has_twitter:
            score += 3.0

        # Telegram (0-2 points)
        if report.has_telegram:
            score += 2.0

        # Discord (0-1 point)
        if report.has_discord:
            score += 1.0

        report.score = min(10.0, score)
