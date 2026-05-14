"""Lead Scraper CLI entry point.

Examples:
    python cli.py "plumber" "Dallas, Texas" 20
    python cli.py "hvac contractor" "Houston, Texas" 50 --no-websites

    # Multiple locations — separate with semicolons or pass a file:
    python cli.py "dentist" "Austin, TX; Dallas, TX; Houston, TX" 25
    python cli.py "dentist" --locations-file cities.txt 25

    # Multiple keywords — separate with semicolons or pass a file:
    python cli.py "plumber; emergency plumber; drain cleaning" "Dallas, TX" 25
    python cli.py "" "Dallas, TX" 25 --keywords-file keywords.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path as _Path

# Point Playwright to project-local browser cache (avoids sandbox cache clears).
_PW_DIR = _Path(__file__).parent / "backend" / ".playwright"
if _PW_DIR.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DIR))

# Make backend/scraper importable
sys.path.insert(0, str(_Path(__file__).parent / "backend"))

from scraper.config import settings
from scraper.pipeline import run_pipeline
from scraper.utils import get_logger


def _progress(stage: str, current: int, total: int, message: str) -> None:
    bar = ""
    if total:
        pct = int(100 * current / total)
        filled = pct // 4
        bar = "[" + "█" * filled + "·" * (25 - filled) + f"] {pct:3d}%"
    print(f"  {stage:<14} {bar}  {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lead-scraper",
        description="Scrape Google Maps + websites + Meta Ad Library into a clean CSV.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Business type / keyword(s). Use ';' or newlines for multiple.",
    )
    parser.add_argument(
        "location",
        nargs="?",
        default="",
        help="Location, e.g. 'Dallas, Texas'. Use ';' or newlines for multiple.",
    )
    parser.add_argument(
        "max_results", nargs="?", type=int, default=20,
        help="Results per (keyword × location). Maps caps at ~120–200.",
    )
    parser.add_argument("--keywords-file", help="Path to a file with one keyword per line")
    parser.add_argument("--locations-file", help="Path to a file with one location per line")
    parser.add_argument("--output", "-o", help="Output basename (no extension)")
    parser.add_argument("--no-websites", action="store_true", help="Skip website enrichment")
    parser.add_argument("--no-meta", action="store_true", help="Skip Meta ads check")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args(argv)
    if args.debug:
        settings.debug = True
        settings.log_level = "DEBUG"

    from pathlib import Path

    keywords: str = args.query
    if args.keywords_file:
        kw_text = Path(args.keywords_file).read_text(encoding="utf-8")
        keywords = (keywords + "\n" + kw_text) if keywords else kw_text

    locations: str = args.location
    if args.locations_file:
        loc_text = Path(args.locations_file).read_text(encoding="utf-8")
        locations = (locations + "\n" + loc_text) if locations else loc_text

    logger = get_logger("scraper", settings.log_level)
    logger.info(
        "Lead Scraper — keywords=%r locations=%r limit=%d",
        keywords, locations, args.max_results,
    )

    result = run_pipeline(
        query=keywords,
        location=locations,
        max_results=args.max_results,
        skip_websites=args.no_websites,
        skip_meta=args.no_meta,
        output_basename=args.output,
        progress=_progress,
    )

    if not result.businesses:
        logger.warning("No businesses returned.")
        return 1

    print(f"\n✓ {len(result.businesses)} leads saved to {result.csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
