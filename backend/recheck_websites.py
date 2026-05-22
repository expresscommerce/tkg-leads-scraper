"""Re-run website enrichment on an existing _maps.csv.

Runs two passes:
  1. Concurrent requests (fast) — same as the main pipeline
  2. Playwright fallback (slower) — for sites that blocked requests

Usage:
    python backend/recheck_websites.py backend/output/garage_door_fl_maps.csv
    python backend/recheck_websites.py backend/output/garage_door_fl_maps.csv --limit 10
    python backend/recheck_websites.py backend/output/garage_door_fl_maps.csv --no-meta
    python backend/recheck_websites.py backend/output/garage_door_fl_maps.csv -o out.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from urllib.parse import urljoin

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

_PW_DIR = _THIS_DIR / ".playwright"
if _PW_DIR.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DIR))

from scraper.config import settings                 # noqa: E402
from scraper.maps import Business, CSV_COLUMNS      # noqa: E402
from scraper.meta_ads import check_meta_ads         # noqa: E402
from scraper.utils import (                         # noqa: E402
    extract_emails, extract_facebook,
    extract_phones, get_logger, write_csv,
)
from scraper.website import enrich_websites         # noqa: E402

logger = get_logger("scraper.recheck_websites")


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _load_businesses(path: Path) -> list[Business]:
    """Load a _maps.csv into Business objects. Merges rows for the same business."""
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)

    # Group rows by (name, address) to reconstruct multi-email businesses
    seen: dict[tuple[str, str], Business] = {}
    for row in rows:
        name = (row.get("business_name") or "").strip()
        addr = (row.get("address") or "").strip()
        if not name:
            continue
        key = (name.lower(), addr.lower())

        if key not in seen:
            try:
                rating = float(row["rating"]) if row.get("rating") else None
            except ValueError:
                rating = None
            try:
                reviews = int(row["reviews"]) if row.get("reviews") else None
            except ValueError:
                reviews = None

            b = Business(
                name=name,
                address=addr,
                phone=(row.get("phone") or "").split(" | ")[0].strip(),
                website=(row.get("website") or "").strip(),
                rating=rating,
                reviews=reviews,
                category=(row.get("category") or "").strip(),
                maps_url=(row.get("maps_url") or "").strip(),
                facebook_url=(row.get("facebook_url") or "").strip(),
                running_meta_ads=str(row.get("running_meta_ads", "")).lower() in ("true", "1", "yes"),
                meta_ad_snapshot_url=(row.get("meta_ad_library_url") or "").strip(),
                meta_ad_start_date=(row.get("meta_ad_start_date") or "").strip(),
                meta_ad_copy=(row.get("meta_ad_copy") or "").strip(),
                meta_ad_platforms=(row.get("meta_ad_platforms") or "").strip(),
            )
            seen[key] = b
        else:
            b = seen[key]

        # Re-attach emails found in previous runs (so we don't lose them)
        em = (row.get("email") or "").strip()
        if em and em not in b.emails:
            b.emails.append(em)

    return list(seen.values())


# ── Playwright fallback ───────────────────────────────────────────────────────

def _parse_html_pw(html: str) -> tuple[str, str]:
    """Extract aggregated text + facebook URL from rendered HTML."""
    from bs4 import BeautifulSoup
    aggregated = ""
    fb_found = ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if href.startswith("mailto:"):
                aggregated += " " + href[7:]
            elif href.startswith("tel:"):
                aggregated += " " + href[4:]
            elif ("facebook.com" in href or "fb.com" in href) and not fb_found:
                fb_found = extract_facebook(href) or fb_found
        aggregated += " " + soup.get_text(" ", strip=True)
    except Exception:
        aggregated = html
    return aggregated, fb_found


def _playwright_enrich(businesses: list[Business], progress=None,
                        offset: int = 0, total: int = 0) -> None:
    """Playwright pass: visit sites that returned no data from requests."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    if not businesses:
        return

    logger.info("Playwright pass: enriching %d sites", len(businesses))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=settings.website.user_agent,
            java_script_enabled=True,
        )
        page = ctx.new_page()

        for i, biz in enumerate(businesses, 1):
            base = biz.website.strip()
            if not base.startswith(("http://", "https://")):
                base = "https://" + base

            aggregated = ""
            fb_found = biz.facebook_url or ""

            for path in settings.website.pages:
                url = urljoin(base + "/", path.lstrip("/")) if path else base
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    html = page.content()
                    chunk, fb = _parse_html_pw(html)
                    aggregated += " " + chunk
                    if fb and not fb_found:
                        fb_found = fb
                except PWTimeout:
                    logger.info("Playwright timeout: %s", url)
                    if not aggregated:
                        break   # base page timed out — skip sub-pages
                except Exception as exc:
                    logger.info("Playwright error %s: %s", url, exc)
                    continue

            if aggregated.strip():
                new_emails = extract_emails(aggregated)
                if new_emails:
                    biz.emails = new_emails
                    logger.info("Playwright HIT: %s → %s", biz.name, new_emails)
                else:
                    logger.info("Playwright no emails: %s", biz.name)
                extra = extract_phones(aggregated)
                if extra:
                    biz.extra_phones = [p for p in extra if p != biz.phone]
                if fb_found:
                    biz.facebook_url = fb_found
            else:
                logger.info("Playwright no data: %s", biz.name)

            if progress:
                try:
                    progress(
                        "playwright",
                        offset + i,
                        total,
                        f"[PW] {biz.name[:45]} — {len(biz.emails)} email(s)",
                    )
                except Exception:
                    pass

        ctx.close()
        browser.close()


# ── Progress helper ───────────────────────────────────────────────────────────

def _make_progress(total: int):
    bar_width = 25

    def _progress(stage: str, current: int, _total: int, msg: str):
        pct = current / max(_total, 1)
        filled = int(bar_width * pct)
        bar = "█" * filled + "·" * (bar_width - filled)
        print(f"\r  {stage:<14} [{bar}] {pct:5.0%}  {msg[:55]}", end="", flush=True)
        if current >= _total:
            print()

    return _progress


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-run website enrichment on a _maps.csv.")
    ap.add_argument("csv", help="Path to the _maps.csv file to re-enrich.")
    ap.add_argument(
        "-o", "--output", default="",
        help="Output CSV path (default: same dir, _websites.csv suffix).",
    )
    ap.add_argument(
        "--limit", type=int, default=0,
        help="Process at most N unique businesses (for smoke-testing).",
    )
    ap.add_argument(
        "--no-meta", action="store_true",
        help="Skip the Meta Ad Library check.",
    )
    ap.add_argument(
        "--no-playwright", action="store_true",
        help="Skip the Playwright fallback pass.",
    )

    args = ap.parse_args(argv)

    src = Path(args.csv).expanduser().resolve()
    if not src.exists():
        ap.error(f"CSV not found: {src}")

    # Default output path
    if args.output:
        dst = Path(args.output).expanduser().resolve()
    else:
        stem = src.stem
        if stem.endswith("_maps"):
            stem = stem[:-5]
        dst = src.parent / f"{stem}_websites.csv"

    businesses = _load_businesses(src)
    if not businesses:
        ap.error("No valid business rows found in CSV.")

    if args.limit:
        businesses = businesses[: args.limit]

    total = len(businesses)
    print(f"\nRe-enriching {total} unique businesses from {src.name} …\n", flush=True)

    _progress = _make_progress(total)

    # ── Pass 1: requests ──────────────────────────────────────────────────────
    print("Pass 1 — requests-based enrichment\n")
    enrich_websites(businesses, progress=_progress)

    no_email_after_requests = [b for b in businesses if not b.emails and b.website]
    print(
        f"\nAfter requests pass: "
        f"{sum(1 for b in businesses if b.emails)}/{total} have emails. "
        f"{len(no_email_after_requests)} sites need Playwright.\n"
    )

    # ── Pass 2: Playwright fallback ───────────────────────────────────────────
    if no_email_after_requests and not args.no_playwright:
        print(f"Pass 2 — Playwright fallback for {len(no_email_after_requests)} blocked sites\n")
        _playwright_enrich(
            no_email_after_requests,
            progress=_progress,
            offset=total - len(no_email_after_requests),
            total=total,
        )
        print(
            f"\nAfter Playwright pass: "
            f"{sum(1 for b in businesses if b.emails)}/{total} have emails.\n"
        )

    # ── Pass 3: Meta ads (optional) ───────────────────────────────────────────
    if not args.no_meta:
        print("Pass 3 — Meta Ad Library check\n")
        try:
            check_meta_ads(businesses, progress=_progress)
        except Exception as exc:
            logger.error("Meta check failed: %s — continuing without ad data", exc)
        print()

    # ── Write output ──────────────────────────────────────────────────────────
    rows_out: list[dict] = []
    for b in businesses:
        rows_out.extend(b.to_rows())

    # Back up old output if it exists
    if dst.exists():
        bak = dst.with_suffix(".bak.csv")
        dst.rename(bak)
        print(f"Backed up previous output → {bak.name}")

    write_csv(dst, rows_out, CSV_COLUMNS)

    emails_found = sum(1 for r in rows_out if r.get("email"))
    print(f"\nSaved {len(rows_out)} rows ({emails_found} with emails) → {dst}")


if __name__ == "__main__":
    main()
