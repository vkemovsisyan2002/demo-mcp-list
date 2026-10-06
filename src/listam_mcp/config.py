"""Runtime configuration, overridable through environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _parse_rates(raw: str | None) -> dict[str, float]:
    """Parse 'USD=363.5,EUR=425,RUB=4.3' into {'USD': 363.5, ...} (AMD per unit)."""
    rates = {"AMD": 1.0, "USD": 363.5, "EUR": 425.0, "RUB": 4.3}
    if raw:
        for part in raw.split(","):
            if "=" in part:
                code, value = part.split("=", 1)
                try:
                    rates[code.strip().upper()] = float(value)
                except ValueError:
                    pass
    return rates


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://www.list.am"
    lang: str = field(default_factory=lambda: os.environ.get("LISTAM_LANG", "en"))
    # Minimum seconds between two requests to list.am. Be polite.
    min_interval: float = field(default_factory=lambda: _env_float("LISTAM_MIN_INTERVAL", 2.5))
    cache_ttl: float = field(default_factory=lambda: _env_float("LISTAM_CACHE_TTL", 300))
    timeout: float = field(default_factory=lambda: _env_float("LISTAM_TIMEOUT", 20))
    max_pages: int = 5
    # list.am is behind Cloudflare, which sometimes 403s a request as a short-lived,
    # burst-triggered block; retry that many times (with growing backoff) before giving up.
    max_retries: int = field(default_factory=lambda: int(_env_float("LISTAM_MAX_RETRIES", 2)))
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "LISTAM_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0 Safari/537.36 listam-mcp",
        )
    )
    # Approximate AMD value of one unit of each currency, used only for
    # cross-currency price filtering. Override with LISTAM_RATES.
    rates: dict[str, float] = field(
        default_factory=lambda: _parse_rates(os.environ.get("LISTAM_RATES"))
    )
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("LISTAM_DATA_DIR", Path.home() / ".listam-mcp")
        )
    )


settings = Settings()
