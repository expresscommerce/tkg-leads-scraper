"""Google Maps business scraper using Playwright."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

from .config import settings
from .utils import get_logger

logger = get_logger("scraper.maps")


@dataclass
class Business:
    name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: Optional[float] = None
    reviews: Optional[int] = None
    category: str = ""
    maps_url: str = ""
    # populated by later stages
    emails: list[str] = field(default_factory=list)
    extra_phones: list[str] = field(default_factory=list)
    facebook_url: str = ""
    running_meta_ads: bool = False
    meta_ad_count: int = 0
    meta_ad_snapshot_url: str = ""
    meta_ad_start_date: str = ""
    meta_ad_copy: str = ""
    meta_ad_platforms: str = ""

    def all_phones(self) -> list[str]:
        """Maps phone first, then any website-discovered phones.

        Deduped by digits-only key so `(555) 123-4567` and `555-123-4567`
        collapse to a single entry (the first-seen format wins).
        """
        seen: set[str] = set()
        out: list[str] = []
        for raw in [self.phone, *self.extra_phones]:
            p = (raw or "").strip()
            if not p:
                continue
            key = re.sub(r"\D+", "", p)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def _row_with_email(self, email: str) -> dict[str, object]:
        """Build a row dict whose key order matches CSV_COLUMNS exactly."""
        return {
            "business_name": self.name,
            "category": self.category,
            "address": self.address,
            "phone": " | ".join(self.all_phones()),
            "website": self.website,
            "rating": self.rating if self.rating is not None else "",
            "reviews": self.reviews if self.reviews is not None else "",
            "email": email,
            "facebook_url": self.facebook_url,
            "running_meta_ads": self.running_meta_ads,
            "meta_ad_count": self.meta_ad_count,
            "meta_ad_library_url": self.meta_ad_snapshot_url,
            "meta_ad_start_date": self.meta_ad_start_date,
            "meta_ad_copy": self.meta_ad_copy,
            "meta_ad_platforms": self.meta_ad_platforms,
            "maps_url": self.maps_url,
        }

    def to_rows(self) -> list[dict[str, object]]:
        """Explode this business into one row per email.

        Businesses with no emails still produce a single row with `email`
        left blank, so they're not lost from the CSV.
        """
        if not self.emails:
            return [self._row_with_email("")]
        return [self._row_with_email(em) for em in self.emails]


CSV_COLUMNS = [
    "business_name",
    "category",
    "address",
    "phone",
    "website",
    "rating",
    "reviews",
    "email",
    "facebook_url",
    "running_meta_ads",
    "meta_ad_count",
    "meta_ad_library_url",
    "meta_ad_start_date",
    "meta_ad_copy",
    "meta_ad_platforms",
    "maps_url",
]


def _parse_rating_block(text: str) -> tuple[Optional[float], Optional[int]]:
    """Parse strings like '4.8(123)' or '4.8 stars 1,234 Reviews'."""
    if not text:
        return None, None
    m = re.search(r"(\d+\.\d+)\s*(?:stars?)?\s*\(?([\d,]+)\)?", text)
    if not m:
        # fall back to just rating
        r = re.search(r"(\d+\.\d+)", text)
        return (float(r.group(1)) if r else None), None
    return float(m.group(1)), int(m.group(2).replace(",", ""))


def search_google_maps(
    query: str,
    location: str,
    max_results: int = 20,
    progress=None,
) -> list[Business]:
    """Scrape Google Maps for business listings.

    Args:
        query: e.g. "plumber"
        location: e.g. "Dallas, Texas"
        max_results: target count
        progress: optional callable(stage:str, current:int, total:int, msg:str)

    Returns:
        List of `Business` records.
    """
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    search_term = f"{query} in {location}".strip()
    url = f"https://www.google.com/maps/search/{quote_plus(search_term)}"
    logger.info("Maps search: %s", search_term)

    results: list[Business] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.maps.headless)
        ctx = browser.new_context(
            user_agent=settings.website.user_agent,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(settings.maps.nav_timeout_ms)

        try:
            page.goto(url, wait_until="domcontentloaded")
            # Accept consent if shown
            try:
                page.get_by_role("button", name=re.compile("accept|agree|i agree", re.I)).first.click(timeout=3000)
            except Exception:
                pass

            # Wait for results feed
            feed_selector = 'div[role="feed"]'
            try:
                page.wait_for_selector(feed_selector, timeout=15_000)
            except PWTimeout:
                logger.warning("No results feed appeared; query may have returned a single place.")
                # Try single-place extraction
                single = _extract_single_place(page)
                if single:
                    results.append(single)
                return results

            feed = page.locator(feed_selector)

            # Scroll until enough results or end-of-list
            seen_count = 0
            stagnant = 0
            for i in range(settings.maps.max_scrolls):
                cards = feed.locator('a[href*="/maps/place/"]')
                count = cards.count()
                if progress:
                    progress("maps", min(count, max_results), max_results, f"Loaded {count} listings")
                if count >= max_results:
                    break
                if count == seen_count:
                    stagnant += 1
                    if stagnant >= 3:
                        break
                else:
                    stagnant = 0
                seen_count = count
                feed.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
                time.sleep(settings.maps.scroll_pause)

            # Iterate through cards and click each to extract details
            cards = feed.locator('a[href*="/maps/place/"]')
            total = min(cards.count(), max_results)
            for i in range(total):
                try:
                    card = cards.nth(i)
                    name = (card.get_attribute("aria-label") or "").strip()
                    href = card.get_attribute("href") or ""

                    biz = Business(name=name, maps_url=href)

                    # Click to open details panel
                    try:
                        card.scroll_into_view_if_needed(timeout=3000)
                        card.click(timeout=5000)
                        page.wait_for_timeout(900)
                    except Exception:
                        pass

                    _extract_details(page, biz)
                    if biz.name:
                        results.append(biz)
                    if progress:
                        progress("maps", i + 1, total, f"Captured {biz.name or 'listing'}")
                except Exception as exc:
                    logger.debug("Card %s failed: %s", i, exc)
                    continue
        finally:
            ctx.close()
            browser.close()

    logger.info("Maps captured %d businesses", len(results))
    return results


def _extract_details(page, biz: Business) -> None:
    """Extract details from currently-open Google Maps place panel."""
    try:
        rating_text = page.locator('div.F7nice').first.inner_text(timeout=2000)
        biz.rating, biz.reviews = _parse_rating_block(rating_text)
    except Exception:
        pass

    # Category (first jsaction button below title often holds category)
    try:
        cat = page.locator('button[jsaction*="category"]').first.inner_text(timeout=1500)
        biz.category = cat.strip()
    except Exception:
        pass

    # Buttons with data-item-id reveal address / phone / website
    for item in page.locator('[data-item-id]').all():
        try:
            item_id = item.get_attribute("data-item-id") or ""
            label = item.get_attribute("aria-label") or ""
        except Exception:
            continue
        if not label:
            continue
        if item_id == "address" or label.lower().startswith("address"):
            biz.address = label.split(":", 1)[-1].strip()
        elif item_id == "authority" or "website" in label.lower():
            try:
                href = item.get_attribute("href")
                if href and href.startswith("http"):
                    biz.website = href
                else:
                    biz.website = label.split(":", 1)[-1].strip()
            except Exception:
                pass
        elif item_id.startswith("phone") or "phone" in label.lower():
            biz.phone = label.split(":", 1)[-1].strip()


def _extract_single_place(page) -> Optional[Business]:
    """When Maps lands directly on a place, extract that one record."""
    try:
        title = page.locator("h1").first.inner_text(timeout=3000)
    except Exception:
        return None
    biz = Business(name=title.strip(), maps_url=page.url)
    _extract_details(page, biz)
    return biz if biz.name else None
