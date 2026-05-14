"""Meta Ad Library checker — scrapes the public website directly (no API).

Strategy: query the Ad Library with `search_type=page` (advertiser search)
rather than `keyword_unordered` (which searches *inside* ad text and almost
always returns 0 for local SMBs). When we already know the business's
Facebook page slug we use that as the search term — it matches the advertiser
directly. The active-ad count is read from the "Active ads · N" badge that
the Ad Library renders on every advertiser card.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote_plus, urlparse

from .config import settings
from .maps import Business
from .utils import get_logger

logger = get_logger("scraper.meta")

_AD_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={country}"
    "&q={query}&search_type=page&media_type=all"
)

# Pulled out of `Active ads · 12` / `Active ads: 12` / `Active ads 12` etc.
_ACTIVE_ADS_RE = re.compile(r"Active\s+ads[^\d]{0,6}(\d[\d,]*)", re.I)
_RESULT_COUNT_RE = re.compile(r"~?\s*([\d,]+)\s+result", re.I)


def _build_url(term: str) -> str:
    return _AD_LIBRARY_URL.format(
        country=settings.meta.country,
        query=quote_plus(term),
    )


def _fb_slug(url: str) -> str:
    """Extract the page username from a facebook.com URL.

    Returns the slug for `facebook.com/<slug>` URLs. For `profile.php?id=...`
    style URLs we return an empty string — those need the numeric ID and the
    advertiser search by name is a better bet.
    """
    if not url:
        return ""
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    if "facebook.com" not in (parts.netloc or "") and "fb.com" not in (parts.netloc or ""):
        return ""
    path = (parts.path or "").strip("/")
    if not path or path.startswith("profile.php"):
        return ""
    # First path segment is the page username (`/precisiongaragedoor/posts` → slug)
    slug = path.split("/", 1)[0]
    # Skip obvious non-page paths (sharer, pages directory, etc.)
    if slug.lower() in {"pages", "sharer", "plugins", "tr", "dialog", "people"}:
        return ""
    return slug


def _parse_active_ads(text: str) -> int:
    """Find the first `Active ads · N` badge in the advertiser card text."""
    if not text:
        return 0
    m = _ACTIVE_ADS_RE.search(text)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def _parse_result_count(text: str) -> int:
    """Fallback: phrases like '~12 results' or '5 results'."""
    if not text:
        return 0
    m = _RESULT_COUNT_RE.search(text)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def _search_terms(biz: Business) -> list[str]:
    """Ordered list of advertiser-search terms to try for a business."""
    terms: list[str] = []
    slug = _fb_slug(biz.facebook_url)
    if slug:
        terms.append(slug)
    name = (biz.name or "").strip()
    if name and name not in terms:
        terms.append(name)
    return terms


def check_meta_ads(businesses: list[Business], progress=None) -> list[Business]:
    """Visit the Meta Ad Library for each business and flag active advertisers.

    Uses Playwright to render the public Ad Library page — no API token required.
    """
    if not settings.meta.enabled:
        logger.info("Meta Ad Library check disabled (META_ENABLED=0); skipping.")
        return businesses

    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    total = len(businesses)
    if total == 0:
        return businesses

    logger.info("Checking %d businesses against the Meta Ad Library", total)
    delay = max(settings.meta.delay_seconds, 0.5)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.meta.headless)
        ctx = browser.new_context(
            user_agent=settings.website.user_agent,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        for idx, biz in enumerate(businesses, 1):
            terms = _search_terms(biz)
            if not terms:
                if progress:
                    progress("meta", idx, total, f"{biz.name[:40]} — skipped (no name)")
                continue

            count = 0
            last_url = ""
            for term in terms:
                url = _build_url(term)
                last_url = url
                try:
                    page.goto(url, wait_until="domcontentloaded")

                    # Dismiss cookie / login dialogs if they appear
                    for label in ("Allow all cookies", "Decline optional cookies", "Close", "Not now"):
                        try:
                            page.get_by_role("button", name=re.compile(label, re.I)).first.click(timeout=1200)
                        except Exception:
                            pass

                    # Wait for either an advertiser card or a no-results message.
                    try:
                        page.wait_for_selector(
                            "text=/Active ads|No ads to show|0 results|Page not available/i",
                            timeout=10_000,
                        )
                    except PWTimeout:
                        pass
                    # Small settle to let the React app populate the badge
                    page.wait_for_timeout(800)

                    body = page.inner_text("body", timeout=4000)
                    count = _parse_active_ads(body) or _parse_result_count(body)
                    if count > 0:
                        break
                except Exception as exc:
                    logger.debug("Meta lookup failed for %s (%s): %s", biz.name, term, exc)

            if count > 0:
                biz.running_meta_ads = True
                biz.meta_ad_snapshot_url = last_url
                biz.meta_ad_count = count

                sample = _extract_first_ad(page)
                if sample:
                    biz.meta_ad_start_date = sample.get("started", "")
                    biz.meta_ad_copy = sample.get("copy", "")
                    biz.meta_ad_platforms = sample.get("platforms", "")
            else:
                # Keep snapshot URL of the last query so a human can verify
                biz.meta_ad_snapshot_url = last_url

            if progress:
                progress(
                    "meta",
                    idx,
                    total,
                    f"{biz.name[:40]} — {count} active ad(s)" if count else f"{biz.name[:40]} — no ads",
                )

            time.sleep(delay)

        ctx.close()
        browser.close()

    return businesses


def _extract_first_ad(page) -> dict[str, str]:
    """Grab a small sample from the first ad card if available."""
    sample: dict[str, str] = {}
    try:
        text = page.locator('div[role="main"]').inner_text(timeout=2500)
    except Exception:
        return sample

    started = re.search(r"Started running on\s+([A-Za-z0-9, ]+)", text)
    if started:
        sample["started"] = started.group(1).strip()

    plat = re.search(r"Platforms\s*([A-Za-z, ]+)", text)
    if plat:
        sample["platforms"] = plat.group(1).strip()

    # First non-empty paragraph after the header is usually the ad copy
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        if 30 < len(ln) < 280 and not ln.lower().startswith(("library id", "started", "platforms")):
            sample["copy"] = ln
            break
    return sample
