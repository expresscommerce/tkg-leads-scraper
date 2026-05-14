"""Shared helpers: logging, regex extraction, slugs, CSV I/O."""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})"
)
FB_RE = re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9_.\-/]+", re.I)


def get_logger(name: str = "scraper", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
                "%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


# Local-part substrings that mark a junk / no-reply mailbox.
JUNK_LOCAL_SUBSTRINGS = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "abuse@",
)

# Domains we never want — CMS / error-tracking noise and placeholders.
JUNK_DOMAINS = {
    # Wix
    "wix.com", "wixpress.com", "sentry-next.wixpress.com",
    # Other platforms
    "sentry.io", "wordpress.org", "wordpress.com",
    "squarespace.com", "godaddy.com",
    "schema.org", "w3.org", "shopify.com",
    # Placeholder / example domains
    "example.com", "example.org", "example.net",
    "domain.com", "yourdomain.com", "email.com",
    "test.com", "mail.com", "site.com",
    "sentry.wixpress.com",
}

# Image extensions sometimes caught by the email regex (filename hashes).
_BAD_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp")


def _is_junk_email(em: str) -> bool:
    if "@" not in em:
        return True
    local, _, domain = em.partition("@")
    if any(b in local for b in JUNK_LOCAL_SUBSTRINGS):
        return True
    if domain in JUNK_DOMAINS:
        return True
    # Sometimes Sentry / WiX hash IDs leak in like "abc123@sentry-next.wixpress.com"
    if "wixpress.com" in domain or "sentry" in domain:
        return True
    return False


def extract_emails(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_RE.findall(text):
        em = match.lower().strip(".,;:")
        if em.endswith(_BAD_SUFFIXES):
            continue
        if _is_junk_email(em):
            continue
        if len(em) >= 100 or em in seen:
            continue
        seen.add(em)
        out.append(em)
    return out


def extract_phones(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for a, b, c in PHONE_RE.findall(text):
        formatted = f"({a}) {b}-{c}"
        if formatted not in seen:
            seen.add(formatted)
            out.append(formatted)
    return out


def extract_facebook(text: str) -> str:
    if not text:
        return ""
    match = FB_RE.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip("/").split("?")[0]
    # Filter helper urls
    bad = ("/sharer", "/plugins", "/tr", "/dialog")
    if any(b in url for b in bad):
        return ""
    return url


def domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in " ,_-":
            out.append("_")
    return "".join(out).strip("_") or "leads"


def default_filename(query: str, location: str) -> str:
    parts = [slugify(query)[:24], slugify(location.split(",")[0])[:24]]
    parts = [p for p in parts if p and p != "leads"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return "_".join(parts + [stamp]) if parts else f"leads_{stamp}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def parse_locations(value: str | list[str]) -> list[str]:
    """Accept a string (newline/comma-separated) or list and return a clean list."""
    if isinstance(value, list):
        raw = value
    else:
        # Split only on newlines / semicolons / pipes so that "Dallas, Texas"
        # stays intact. Users separate locations with newlines.
        raw = re.split(r"[\n;|]+", value or "")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        s = (item or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def join_unique(items: Iterable[str], sep: str = " | ") -> str:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return sep.join(out)
