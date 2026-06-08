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
    instagram_url: str = ""
    tiktok_url: str = ""
    running_meta_ads: bool = False
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
            "instagram_url": self.instagram_url,
            "tiktok_url": self.tiktok_url,
            "running_meta_ads": self.running_meta_ads,
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
    "instagram_url",
    "tiktok_url",
    "running_meta_ads",
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
        # pyrefly: ignore [missing-import]
    from playwright.sync_api import TimeoutError as PWTimeout
        # pyrefly: ignore [missing-import]
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

            # Extract everything from the visible cards in a single JS
            # pass — no per-card clicking. Each card's container holds
            # name, rating, reviews, category, address, phone, and the
            # website link inline, so we can read it all without ever
            # opening the side panel. This is dramatically faster and
            # also sidesteps the stale-panel bug entirely (no panel,
            # no panel desync).
            raw_cards = page.evaluate(
                """
                (max) => {
                  const feed = document.querySelector('div[role=\"feed\"]');
                  if (!feed) return [];
                  const anchors = Array.from(
                    feed.querySelectorAll('a[href*=\"/maps/place/\"]')
                  ).slice(0, max);
                  return anchors.map(a => {
                    // The card container is usually the anchor's parent.
                    // Walk up if needed to find the wrapper that holds
                    // both the link and the metadata siblings.
                    let card = a.parentElement || a;
                    for (let i = 0; i < 4; i++) {
                      if (card.querySelector('a[data-value=\"Website\"]') ||
                          card.querySelector('span[role=\"img\"][aria-label*=\"star\" i]')) {
                        break;
                      }
                      if (card.parentElement) card = card.parentElement;
                    }
                    const text = (card.innerText || '').trim();
                    // Website is exposed as a separate anchor with this
                    // data-value attribute on Maps result cards.
                    const webEl = card.querySelector('a[data-value=\"Website\"]');
                    const website = webEl ? webEl.getAttribute('href') || '' : '';
                    // Rating widget exposes "X.Y stars N Reviews" via
                    // aria-label on a span.
                    const rateEl = card.querySelector(
                      'span[role=\"img\"][aria-label*=\"star\" i]'
                    );
                    const ratingLabel = rateEl
                      ? rateEl.getAttribute('aria-label') || ''
                      : '';
                    return {
                      name: a.getAttribute('aria-label') || '',
                      href: a.getAttribute('href') || '',
                      text,
                      website,
                      ratingLabel,
                    };
                  });
                }
                """,
                max_results,
            )
            total = len(raw_cards)
            for i, raw in enumerate(raw_cards):
                try:
                    biz = _build_from_card(raw)
                    if biz and biz.name:
                        results.append(biz)
                    if progress:
                        progress(
                            "maps",
                            i + 1,
                            total,
                            f"Captured {raw.get('name') or 'listing'}",
                        )
                except Exception as exc:
                    logger.debug("Card %s parse failed: %s", i, exc)
                    continue

            # Second pass: addresses are no longer in the card text on modern
            # Google Maps. Visit each business's place URL and read the address
            # from the side panel's `[data-item-id="address"]` button. Slower,
            # but the only reliable way to get a full street address.
            for i, biz in enumerate(results):
                if biz.address or not biz.maps_url:
                    continue
                try:
                    addr = _fetch_address(page, biz.maps_url)
                    if addr:
                        biz.address = addr
                except Exception as exc:
                    logger.debug("Address fetch failed for %s: %s", biz.name, exc)
                if progress:
                    progress(
                        "maps",
                        i + 1,
                        len(results),
                        f"Address: {biz.name[:40]} — {biz.address[:50] or 'n/a'}",
                    )
        finally:
            ctx.close()
            browser.close()

    logger.info("Maps captured %d businesses", len(results))
    return results


_CARD_PHONE_RE = re.compile(
    r"(\+?1?[\s.\-]*\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4})"
)


def _fetch_address(page, maps_url: str) -> str:
    """Open a place's Maps URL and return its address from the side panel.

    Reads the `aria-label` of the `[data-item-id="address"]` button which
    Google renders as "Address: 123 Main St, Houston, TX 77002". Falls
    back to similar selectors when the markup shifts.
    """
    # pyrefly: ignore [missing-import]
    from playwright.sync_api import TimeoutError as PWTimeout

    try:
        page.goto(maps_url, wait_until="domcontentloaded")
    except Exception:
        return ""

    try:
        page.wait_for_selector('[data-item-id="address"]', timeout=8000)
    except PWTimeout:
        pass

    selectors = (
        '[data-item-id="address"]',
        'button[aria-label^="Address"]',
        'button[data-tooltip="Copy address"]',
    )
    for sel in selectors:
        try:
            el = page.locator(sel).first
            label = el.get_attribute("aria-label", timeout=1500) or ""
            if label:
                return label.split(":", 1)[-1].strip()
            text = (el.inner_text(timeout=1500) or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def _build_from_card(raw: dict) -> Optional[Business]:
    """Parse a single card-extraction record into a `Business`.

    The card's `text` is the full innerText of the result tile, which
    looks roughly like:

        ABC Garage Door Repair
        4.8(123)
        Garage door supplier
        Open · Closes 5 PM
        123 Main St, Dallas, TX
        "Quote excerpt..."
        (214) 555-1234

    We pull rating/reviews from the dedicated `ratingLabel`, phone via
    regex, and treat the line that mentions a state / zip / "St"-style
    suffix as the address. The line just before the address (and not
    matching status / hours / quote markers) is the category.
    """
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    href = raw.get("href") or ""
    text = raw.get("text") or ""
    website = (raw.get("website") or "").strip()

    biz = Business(name=name, maps_url=href, website=website)

    # Rating + reviews from the dedicated aria-label, falls back to text
    rate_label = raw.get("ratingLabel") or ""
    biz.rating, biz.reviews = _parse_rating_block(rate_label or text)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Phone — first regex match anywhere in the card text
    for ln in lines:
        m = _CARD_PHONE_RE.search(ln)
        if m:
            biz.phone = m.group(1).strip()
            break

    # Address heuristic: a line containing a comma + (state code or zip
    # or "St"/"Rd"/"Ave"/"Blvd" etc.). Prefer the last such line in case
    # there's a quoted review that also mentions a place.
    addr_idx = -1
    for i, ln in enumerate(lines):
        if "," in ln and (
            re.search(r"\b[A-Z]{2}\b", ln) or
            re.search(r"\b\d{4,6}\b", ln) or
            re.search(r"\b(St|Rd|Ave|Blvd|Dr|Ln|Way|Hwy|Pkwy|Ct)\b\.?", ln)
        ):
            addr_idx = i
    if addr_idx >= 0:
        biz.address = lines[addr_idx]

    # Category: the line(s) above the address that aren't the name,
    # rating, hours/status ("Open · ...", "Closed"), or a quoted review.
    if addr_idx > 0:
        for j in range(addr_idx - 1, -1, -1):
            ln = lines[j]
            low = ln.lower()
            if ln == name:
                continue
            if re.match(r"^\d+(\.\d+)?\s*\(", ln):  # rating "4.8(123)"
                continue
            if low.startswith(("open", "closed", "closes", "opens", "·")):
                continue
            if ln.startswith(('"', '“')):
                continue
            biz.category = ln
            break

    return biz


def _panel_place_id(href: str) -> str:
    """Extract the stable place id chunk from a Maps `/place/` URL.

    Two URLs for the same place share the segment after `!1s` (e.g.
    `0x...:0x...`). Comparing this is far more reliable than matching the
    name, which gets truncated / decorated in the panel.
    """
    if not href:
        return ""
    m = re.search(r"!1s([^!?/]+)", href)
    if m:
        return m.group(1)
    # Fallback: the human-readable slug between `/place/` and the next `/`
    m = re.search(r"/place/([^/]+)/", href)
    return m.group(1) if m else ""


def _read_panel_h1(page) -> str:
    """Read the side-panel title quickly. Empty string on failure."""
    try:
        return (page.locator("h1").first.inner_text(timeout=200) or "").strip()
    except Exception:
        return ""


def _wait_for_panel(
    page,
    biz: Business,
    prev_h1: str = "",
    timeout_ms: int = 4000,
) -> str:
    """Block until the side panel's `<h1>` differs from `prev_h1`.

    We don't try to *match* the new h1 to the card name — Maps truncates,
    decorates, or reformats titles in the panel which made substring
    matching unreliable. Instead we just wait for the title to *change*
    from the previous card's title; that's the cheapest reliable signal
    that the panel has swapped. Returns the new h1 (or whatever was
    last read on timeout).
    """
    target_id = _panel_place_id(biz.maps_url)
    deadline = time.time() + timeout_ms / 1000.0
    h1 = ""
    while time.time() < deadline:
        h1 = _read_panel_h1(page)
        if h1 and h1 != prev_h1:
            return h1
        if target_id:
            try:
                if _panel_place_id(page.url) == target_id:
                    # URL synced even if h1 is still painting
                    return h1 or prev_h1
            except Exception:
                pass
        page.wait_for_timeout(100)
    return h1


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
