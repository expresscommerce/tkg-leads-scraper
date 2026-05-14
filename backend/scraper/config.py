"""Centralised configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "output"


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    try:
        return int(val) if val else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    try:
        return float(val) if val else default
    except ValueError:
        return default


@dataclass
class MapsSettings:
    headless: bool = True
    scroll_pause: float = 1.2
    max_scrolls: int = 60
    nav_timeout_ms: int = 30_000


@dataclass
class WebsiteSettings:
    timeout: int = 8
    max_workers: int = 6
    pages: tuple[str, ...] = ("", "/contact", "/contact-us", "/about")
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


@dataclass
class MetaAdsSettings:
    """Direct Ad Library scraping (no API). Set META_ENABLED=0 to skip the stage."""

    enabled: bool = True
    country: str = "US"
    headless: bool = True
    delay_seconds: float = 2.0


@dataclass
class Settings:
    output_dir: Path = field(default_factory=lambda: DEFAULT_OUTPUT_DIR)
    log_level: str = "INFO"
    debug: bool = False

    maps: MapsSettings = field(default_factory=MapsSettings)
    website: WebsiteSettings = field(default_factory=WebsiteSettings)
    meta: MetaAdsSettings = field(default_factory=MetaAdsSettings)

    def __post_init__(self) -> None:
        self.log_level = os.environ.get("LOG_LEVEL", self.log_level).upper()
        self.debug = _env_bool("DEBUG", self.debug)

        self.maps.headless = _env_bool("MAPS_HEADLESS", self.maps.headless)
        self.maps.max_scrolls = _env_int("MAPS_MAX_SCROLLS", self.maps.max_scrolls)

        self.website.max_workers = _env_int(
            "WEBSITE_MAX_WORKERS", self.website.max_workers
        )
        self.website.timeout = _env_int("WEBSITE_TIMEOUT", self.website.timeout)

        self.meta.enabled = _env_bool("META_ENABLED", self.meta.enabled)
        self.meta.country = os.environ.get("META_AD_COUNTRY", self.meta.country)
        self.meta.headless = _env_bool("META_HEADLESS", self.meta.headless)
        self.meta.delay_seconds = _env_float("META_DELAY_SECONDS", self.meta.delay_seconds)

        out = os.environ.get("OUTPUT_DIR")
        if out:
            self.output_dir = Path(out).expanduser().resolve()

    def ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir


settings = Settings()
