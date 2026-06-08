"""Concurrent website scraper extracting emails, phones, Facebook URLs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urljoin

import requests
    # pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup

from .config import settings
from .maps import Business
from .utils import (
    extract_emails,
    extract_facebook,
    extract_instagram,
    extract_phones,
    extract_tiktok,
    get_logger,
)

logger = get_logger("scraper.website")

# Thread-local session for connection pooling/keep-alive
import threading as _threading

_thread_local = _threading.local()


def _session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": settings.website.user_agent})
        _thread_local.session = s
    return s


def _fetch(url: str, timeout: int) -> Optional[str]:
    try:
        resp = _session().get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
        logger.info("Fetch %s -> HTTP %s", url, resp.status_code)
    except requests.RequestException as exc:
        logger.info("Fetch failed %s: %s", url, type(exc).__name__)
    return None


def _enrich_one(biz: Business) -> Business:
    if not biz.website:
        return biz

    base = biz.website.strip()
    if not base.startswith(("http://", "https://")):
        base = "https://" + base

    aggregated = ""
    fb_found = biz.facebook_url or ""
    insta_found = biz.instagram_url or ""
    tiktok_found = biz.tiktok_url or ""
    # Try the base page first; if it fails, skip the secondary pages
    # (this avoids 4 timeouts per dead site on large runs).
    for idx, path in enumerate(settings.website.pages):
        url = urljoin(base + "/", path.lstrip("/")) if path else base
        html = _fetch(url, settings.website.timeout)
        if not html:
            if idx == 0:
                # Base page unreachable → don't bother with /contact, /about etc.
                return biz
            continue

        # Use BS4 to also pull mailto/tel hrefs reliably
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if href.startswith("mailto:"):
                    aggregated += " " + href[7:]
                elif href.startswith("tel:"):
                    aggregated += " " + href[4:]
                elif ("facebook.com" in href or "fb.com" in href) and not fb_found:
                    candidate = extract_facebook(href)
                    if candidate:
                        fb_found = candidate
                elif ("instagram.com" in href or "instagr.am" in href) and not insta_found:
                    candidate = extract_instagram(href)
                    if candidate:
                        insta_found = candidate
                elif "tiktok.com" in href and not tiktok_found:
                    candidate = extract_tiktok(href)
                    if candidate:
                        tiktok_found = candidate
            aggregated += " " + soup.get_text(" ", strip=True)
        except Exception:
            aggregated += " " + html

    biz.emails = extract_emails(aggregated)
    extra = extract_phones(aggregated)
    if extra:
        # Keep map phone first; append unique others
        biz.extra_phones = [p for p in extra if p != biz.phone]
    if fb_found:
        biz.facebook_url = fb_found
    if insta_found:
        biz.instagram_url = insta_found
    if tiktok_found:
        biz.tiktok_url = tiktok_found
    return biz


def enrich_websites(businesses: list[Business], progress=None) -> list[Business]:
    """Visit each business website and extract contact info concurrently."""
    targets = [b for b in businesses if b.website]
    if not targets:
        return businesses

    total = len(targets)
    logger.info("Enriching %d websites with %d workers", total, settings.website.max_workers)

    completed = 0
    with ThreadPoolExecutor(max_workers=settings.website.max_workers) as pool:
        futures = {pool.submit(_enrich_one, b): b for b in targets}
        for fut in as_completed(futures):
            completed += 1
            try:
                fut.result()
            except Exception as exc:
                logger.info("Website enrichment error: %s", exc)
            if progress:
                progress("website", completed, total, f"Scanned {completed}/{total} sites")
            # Heartbeat every 25 sites so user sees progress on large runs
            if completed % 25 == 0 or completed == total:
                logger.info("Website progress: %d/%d", completed, total)
    return businesses
