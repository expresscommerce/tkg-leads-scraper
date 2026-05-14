"""High-level pipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import settings
from .maps import CSV_COLUMNS, Business, search_google_maps
from .meta_ads import check_meta_ads
from .utils import default_filename, get_logger, parse_locations, write_csv
from .website import enrich_websites

logger = get_logger("scraper.pipeline")

# stage, current, total, message
ProgressFn = Callable[[str, int, int, str], None]


@dataclass
class PipelineResult:
    businesses: list[Business]
    csv_path: Optional[Path]

    @property
    def rows(self) -> list[dict]:
        """Exploded rows: one per email (or one blank-email row per business)."""
        out: list[dict] = []
        for b in self.businesses:
            out.extend(b.to_rows())
        return out


def _dedupe(businesses: list[Business]) -> list[Business]:
    """Remove duplicates by (name, address) — keeps first occurrence."""
    seen: set[tuple[str, str]] = set()
    out: list[Business] = []
    for b in businesses:
        key = (b.name.strip().lower(), b.address.strip().lower())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def run_pipeline(
    query: str | list[str],
    location: str | list[str],
    max_results: int = 20,
    *,
    skip_websites: bool = False,
    skip_meta: bool = False,
    output_basename: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
) -> PipelineResult:
    """Run the full lead-generation pipeline.

    Both `query` and `location` accept either a single string or a list /
    newline-/semicolon-separated string. Maps is searched for the cartesian
    product of (keyword × location); results are merged & deduped before
    website enrichment and Meta Ad Library checks.

    Stages: maps (per keyword × location) → dedupe → website → meta → CSV.
    """
    queries = parse_locations(query)  # same parser handles keywords too
    if not queries:
        raise ValueError("at least one query / keyword is required")

    locations = parse_locations(location)
    if not locations:
        raise ValueError("at least one location is required")

    def emit(stage: str, cur: int, tot: int, msg: str = "") -> None:
        if progress:
            try:
                progress(stage, cur, tot, msg)
            except Exception:
                pass

    combos = [(q, loc) for q in queries for loc in locations]
    target_total = max_results * len(combos)
    emit(
        "start",
        0,
        target_total,
        f"Searching {len(queries)} keyword(s) × {len(locations)} location(s) "
        f"= {len(combos)} combination(s)",
    )

    all_businesses: list[Business] = []
    failures: list[str] = []
    for idx, (q, loc) in enumerate(combos, 1):
        logger.info("[%d/%d] %r in %s", idx, len(combos), q, loc)
        emit(
            "maps_location",
            idx,
            len(combos),
            f"({idx}/{len(combos)}) {q} — {loc}",
        )
        try:
            found = search_google_maps(q, loc, max_results, progress=progress)
        except Exception as exc:
            logger.error("Maps search failed for %r in %s: %s", q, loc, exc)
            failures.append(f"{q!r} in {loc}: {exc}")
            # If even the very first search dies, it's almost certainly an
            # environment problem (missing browser, network, rate-limit) — not
            # something that'll fix itself on the next location, so surface it
            # immediately rather than silently producing an empty CSV.
            if idx == 1:
                raise RuntimeError(
                    f"Maps search failed on the first attempt: {exc}"
                ) from exc
            found = []
        # Tag each business with the keyword that surfaced it (handy when
        # downstream users want to see which query a lead came from).
        for b in found:
            if not b.category:
                b.category = q
        all_businesses.extend(found)
        emit(
            "maps_location_done",
            idx,
            len(combos),
            f"{q} / {loc}: {len(found)} found (running total {len(all_businesses)})",
        )

    businesses = _dedupe(all_businesses)
    emit(
        "maps_done",
        len(businesses),
        target_total,
        f"{len(businesses)} unique businesses after dedupe",
    )

    if not businesses:
        return PipelineResult(businesses=[], csv_path=None)

    # Prepare output paths early so we can save intermediate checkpoints
    q_label = queries[0] if len(queries) == 1 else f"{len(queries)}_keywords"
    loc_label = locations[0] if len(locations) == 1 else f"{len(locations)}_locations"
    basename = output_basename or default_filename(q_label, loc_label)
    out_dir = settings.ensure_output_dir()

    def _save_checkpoint(suffix: str) -> Path:
        rows_: list[dict] = []
        for b in businesses:
            rows_.extend(b.to_rows())
        path = write_csv(out_dir / f"{basename}_{suffix}.csv", rows_, CSV_COLUMNS)
        logger.info("Checkpoint saved: %s (%d businesses, %d rows)", path.name, len(businesses), len(rows_))
        return path

    # Checkpoint #1: after maps scraping
    maps_csv = _save_checkpoint("maps")
    emit("maps_saved", len(businesses), len(businesses),
         f"Maps checkpoint saved: {maps_csv.name}")

    if not skip_websites:
        try:
            enrich_websites(businesses, progress=progress)
            emit("website_done", len(businesses), len(businesses), "Website enrichment complete")
            # Checkpoint #2: after website enrichment
            web_csv = _save_checkpoint("websites")
            emit("websites_saved", len(businesses), len(businesses),
                 f"Websites checkpoint saved: {web_csv.name}")
        except Exception as exc:
            logger.error("Website enrichment failed: %s — partial maps data preserved in %s", exc, maps_csv)
            raise

    if not skip_meta:
        try:
            check_meta_ads(businesses, progress=progress)
            emit("meta_done", len(businesses), len(businesses), "Meta ad check complete")
        except Exception as exc:
            logger.error("Meta ads check failed: %s — partial data preserved in checkpoints", exc)
            raise

    # Final CSV
    rows: list[dict] = []
    for b in businesses:
        rows.extend(b.to_rows())
    csv_path = write_csv(out_dir / f"{basename}.csv", rows, CSV_COLUMNS)
    emit("done", len(businesses), len(rows),
         f"Saved {csv_path.name} ({len(rows)} rows from {len(businesses)} businesses)")

    return PipelineResult(businesses=businesses, csv_path=csv_path)
