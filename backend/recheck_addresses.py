"""Backfill missing addresses on an existing CSV by re-opening each business's
saved Google Maps URL and reading the side panel's address field.

The original Maps stage skipped addresses for most businesses because Google
no longer puts the full street address in the result-card text. Each row,
however, has a `maps_url` we can reuse: opening that URL takes us straight
to the place panel where the address is exposed via `[data-item-id="address"]`.

Usage:
    python backend/recheck_addresses.py output/<file>.csv
    python backend/recheck_addresses.py <file>.csv --headed --delay 1.5
    python backend/recheck_addresses.py <file>.csv --only-missing   # default
    python backend/recheck_addresses.py <file>.csv --all             # overwrite all
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import time
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

_PW_DIR = _THIS_DIR / ".playwright"
if _PW_DIR.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DIR))

from scraper.utils import get_logger  # noqa: E402

logger = get_logger("scraper.recheck_addresses")


def _load_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise SystemExit(f"{path} has no header row")
    return fieldnames, rows


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _progress(stage: str, current: int, total: int, message: str) -> None:
    bar = ""
    if total:
        pct = int(100 * current / total)
        filled = pct // 4
        bar = "[" + "█" * filled + "·" * (25 - filled) + f"] {pct:3d}%"
    print(f"  {stage:<6} {bar}  {message}", flush=True)


def _extract_address(page) -> str:
    """Read the address from the currently-open Maps place panel.

    Tries the dedicated `[data-item-id="address"]` button first (its
    aria-label is "Address: 123 Main St, ..."), then falls back to any
    button whose aria-label starts with "Address".
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    # The panel takes a moment to populate after navigation.
    try:
        page.wait_for_selector('[data-item-id="address"]', timeout=8000)
    except PWTimeout:
        pass

    selectors = [
        '[data-item-id="address"]',
        'button[aria-label^="Address"]',
        'button[data-tooltip="Copy address"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            label = el.get_attribute("aria-label", timeout=1500) or ""
            if label:
                # "Address: 123 Main St, Houston, TX 77002" → strip leading label
                return label.split(":", 1)[-1].strip()
            text = el.inner_text(timeout=1500).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="recheck-addresses",
        description="Backfill missing addresses by re-opening saved maps_urls.",
    )
    ap.add_argument("csv", help="Path to the CSV to update")
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    ap.add_argument("--delay", type=float, default=0.8, help="Seconds between lookups")
    ap.add_argument(
        "--all",
        action="store_true",
        help="Re-fetch addresses for every row (default: only rows with empty address).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N unique businesses (smoke test).",
    )
    args = ap.parse_args(argv)

    src = Path(args.csv).expanduser().resolve()
    if not src.exists():
        ap.error(f"CSV not found: {src}")

    lock = src.with_name(f".~lock.{src.name}#")
    if lock.exists():
        ap.error(f"{src.name} is locked. Close it first or delete {lock}.")

    fieldnames, rows = _load_rows(src)
    if "maps_url" not in fieldnames:
        ap.error("CSV is missing the 'maps_url' column — nothing to backfill from.")
    if "address" not in fieldnames:
        ap.error("CSV is missing the 'address' column.")

    # Group rows by maps_url so duplicate (exploded) rows for the same business
    # share a single lookup.
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        url = (r.get("maps_url") or "").strip()
        if not url:
            continue
        if not args.all and (r.get("address") or "").strip():
            # Already has an address; skip unless --all was passed.
            continue
        if url not in groups:
            groups[url] = []
            order.append(url)
        groups[url].append(r)

    if args.limit:
        order = order[: args.limit]

    if not order:
        print("Nothing to do — every row already has an address.", flush=True)
        return 0

    print(
        f"Backfilling addresses for {len(order)} unique businesses "
        f"({sum(len(groups[u]) for u in order)} rows) — "
        f"delay={args.delay}s, headless={not args.headed}",
        flush=True,
    )

    from playwright.sync_api import sync_playwright

    filled = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(20_000)

        for idx, url in enumerate(order, 1):
            grp = groups[url]
            biz_name = (grp[0].get("business_name") or "").strip()
            try:
                page.goto(url, wait_until="domcontentloaded")
                addr = _extract_address(page)
            except Exception as exc:
                logger.debug("Address lookup failed for %s: %s", biz_name, exc)
                addr = ""

            if addr:
                filled += 1
                for r in grp:
                    r["address"] = addr

            _progress(
                "addr",
                idx,
                len(order),
                f"{biz_name[:40]} — {addr[:60] if addr else 'not found'}",
            )
            time.sleep(args.delay)

        ctx.close()
        browser.close()

    backup = src.with_suffix(src.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(src, backup)
        print(f"Backup saved → {backup}", flush=True)

    _write_rows(src, fieldnames, rows)
    print(
        f"\n✓ Updated {src} — filled addresses for {filled}/{len(order)} businesses.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
