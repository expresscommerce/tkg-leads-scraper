"""Re-run the Meta Ad Library stage against an existing CSV and update it in place.

Use this when the main pipeline finished Maps + website enrichment but the
Meta stage produced all-False results (e.g. cookie wall, old keyword-search
strategy, network blip). It re-uses the improved `check_meta_ads()` from
`scraper.meta_ads`, then writes the updated `running_meta_ads`,
`meta_ad_count`, `meta_ad_library_url`, `meta_ad_start_date`, `meta_ad_copy`,
and `meta_ad_platforms` columns back into the same CSV. A `.bak` copy of the
original is saved alongside it.

Usage:
    python backend/recheck_meta.py backend/output/<file>.csv
    python backend/recheck_meta.py <file>.csv --country US --delay 2.5
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

# Make `scraper` importable when run from the repo root or backend/
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

# Point Playwright at the project-local browser cache, same as cli.py
_PW_DIR = _THIS_DIR / ".playwright"
if _PW_DIR.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DIR))

from scraper.config import settings  # noqa: E402
from scraper.maps import Business  # noqa: E402
from scraper.meta_ads import check_meta_ads  # noqa: E402
from scraper.utils import get_logger  # noqa: E402

logger = get_logger("scraper.recheck_meta")

# Columns the Meta stage owns — only these get overwritten on each row.
META_COLUMNS = (
    "running_meta_ads",
    "meta_ad_count",
    "meta_ad_library_url",
    "meta_ad_start_date",
    "meta_ad_copy",
    "meta_ad_platforms",
)


def _load_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise SystemExit(f"{path} has no header row")
    return fieldnames, rows


def _group_businesses(rows: list[dict]) -> list[tuple[Business, list[dict]]]:
    """Collapse exploded (one-per-email) rows back to unique businesses."""
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for r in rows:
        key = (
            (r.get("business_name") or "").strip().lower(),
            (r.get("address") or "").strip().lower(),
        )
        if not key[0]:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    pairs: list[tuple[Business, list[dict]]] = []
    for key in order:
        first = groups[key][0]
        biz = Business(
            name=first.get("business_name", ""),
            address=first.get("address", ""),
            website=first.get("website", ""),
            facebook_url=first.get("facebook_url", ""),
        )
        pairs.append((biz, groups[key]))
    return pairs


def _apply_results(pairs: list[tuple[Business, list[dict]]]) -> None:
    for biz, grp in pairs:
        for r in grp:
            r["running_meta_ads"] = str(bool(biz.running_meta_ads))
            r["meta_ad_count"] = str(biz.meta_ad_count) if biz.meta_ad_count else ""
            # Always refresh the snapshot URL — even on misses we now record the
            # search URL so a human can sanity-check.
            if biz.meta_ad_snapshot_url:
                r["meta_ad_library_url"] = biz.meta_ad_snapshot_url
            if biz.meta_ad_start_date:
                r["meta_ad_start_date"] = biz.meta_ad_start_date
            if biz.meta_ad_copy:
                r["meta_ad_copy"] = biz.meta_ad_copy
            if biz.meta_ad_platforms:
                r["meta_ad_platforms"] = biz.meta_ad_platforms


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="recheck-meta",
        description="Re-run the Meta Ad Library stage against an existing CSV and update it in place.",
    )
    ap.add_argument("csv", help="Path to the CSV to update (e.g. backend/output/leads_websites.csv)")
    ap.add_argument("--country", default=None, help="Override META_AD_COUNTRY (default: settings/env)")
    ap.add_argument("--delay", type=float, default=None, help="Override META_DELAY_SECONDS")
    ap.add_argument("--headed", action="store_true", help="Run Playwright with a visible browser")
    ap.add_argument(
        "--output",
        "-o",
        help="Write to a different CSV instead of editing in place (no .bak is made).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N unique businesses (handy for smoke-testing).",
    )

    args = ap.parse_args(argv)

    src = Path(args.csv).expanduser().resolve()
    if not src.exists():
        ap.error(f"CSV not found: {src}")

    lock = src.with_name(f".~lock.{src.name}#")
    if lock.exists():
        ap.error(
            f"{src.name} is locked (LibreOffice/Excel?). Close it first or delete {lock}."
        )

    if args.country:
        settings.meta.country = args.country
    if args.delay is not None:
        settings.meta.delay_seconds = args.delay
    if args.headed:
        settings.meta.headless = False
    settings.meta.enabled = True  # ignore META_ENABLED=0 here — user asked for it

    fieldnames, rows = _load_rows(src)
    missing = [c for c in META_COLUMNS if c not in fieldnames]
    if missing:
        ap.error(
            f"CSV is missing required Meta column(s): {missing}. "
            "Was this CSV produced by the current pipeline?"
        )

    pairs = _group_businesses(rows)
    if args.limit:
        pairs = pairs[: args.limit]
    if not pairs:
        ap.error("No business rows found in CSV.")

    print(
        f"Re-checking Meta Ad Library for {len(pairs)} unique businesses "
        f"({len(rows)} rows) — country={settings.meta.country}, "
        f"delay={settings.meta.delay_seconds}s, headless={settings.meta.headless}",
        flush=True,
    )

    businesses = [b for b, _ in pairs]
    check_meta_ads(businesses, progress=_progress)

    _apply_results(pairs)

    dest = Path(args.output).expanduser().resolve() if args.output else src
    if dest == src:
        backup = src.with_suffix(src.suffix + ".bak")
        shutil.copy2(src, backup)
        print(f"Backup saved → {backup}", flush=True)

    _write_rows(dest, fieldnames, rows)

    hits = sum(1 for b in businesses if b.running_meta_ads)
    print(
        f"\n✓ Updated {dest} — {hits}/{len(businesses)} businesses found running active ads.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
