"""Clean a previously-produced leads CSV in place.

Applies the same rules as the live pipeline:
  1. Collapse rows that look like the same business (same phone digits OR
     same website domain OR same name+address). First occurrence wins.
  2. Ensure every email appears on at most one business. Later duplicates
     are dropped (the row survives with a blank email if all its emails
     were claimed by an earlier business).

Usage:
    python backend/dedupe_csv.py path/to/leads.csv
    python backend/dedupe_csv.py path/to/leads.csv -o path/to/cleaned.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def _domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _phone_key(phone: str) -> str:
    """Digits-only key. Handles `(555) 123-4567 | 555-987-6543` (multi)."""
    if not phone:
        return ""
    # Take the first phone if pipe-joined
    first = phone.split("|")[0]
    return re.sub(r"\D+", "", first)


def dedupe(rows: list[dict]) -> list[dict]:
    """Two-pass clean.

    Pass 1: assign each row to a *business identity* (first phone / domain /
    name+addr it matches). Rows that resolve to the same identity are kept
    together — they are the email-explosion of one business, not duplicates.

    Pass 2: emit one row per (identity, email). Globally-duplicate emails
    are blanked. Identities that produced no surviving emails still emit a
    single blank-email row so the lead isn't lost.
    """
    # identity_id -> first row seen for that identity (template for blanks)
    identities: dict[int, dict] = {}
    # identity_id -> ordered list of emails (deduped within identity)
    id_emails: dict[int, list[str]] = {}
    # lookups so later rows find the same identity
    by_phone: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    by_name_addr: dict[tuple[str, str], int] = {}
    next_id = 0
    order: list[int] = []  # preserves first-seen order of identities

    for row in rows:
        name = (row.get("business_name") or "").strip().lower()
        if not name:
            continue
        addr = (row.get("address") or "").strip().lower()
        phone_key = _phone_key(row.get("phone") or "")
        dom_key = _domain(row.get("website") or "")
        name_addr_key = (name, addr)

        ident: int | None = None
        if phone_key and phone_key in by_phone:
            ident = by_phone[phone_key]
        elif dom_key and dom_key in by_domain:
            ident = by_domain[dom_key]
        elif name_addr_key in by_name_addr:
            ident = by_name_addr[name_addr_key]

        if ident is None:
            ident = next_id
            next_id += 1
            identities[ident] = row
            id_emails[ident] = []
            order.append(ident)

        # Register every key we now know about for this identity, so a later
        # row that only matches on (e.g.) domain still resolves correctly.
        if phone_key:
            by_phone.setdefault(phone_key, ident)
        if dom_key:
            by_domain.setdefault(dom_key, ident)
        by_name_addr.setdefault(name_addr_key, ident)

        em = (row.get("email") or "").strip().lower()
        if em and em not in id_emails[ident]:
            id_emails[ident].append(em)

    # Pass 2: emit rows. Global email dedupe across identities.
    seen_email: set[str] = set()
    out: list[dict] = []
    for ident in order:
        template = identities[ident]
        kept: list[str] = []
        for em in id_emails[ident]:
            if em in seen_email:
                continue
            seen_email.add(em)
            kept.append(em)
        if not kept:
            blank = dict(template)
            blank["email"] = ""
            out.append(blank)
        else:
            for em in kept:
                row = dict(template)
                row["email"] = em
                out.append(row)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path, help="Existing leads CSV to clean")
    p.add_argument(
        "-o", "--output", type=Path,
        help="Write cleaned CSV here (default: overwrite input with .bak backup)",
    )
    args = p.parse_args()

    if not args.csv.exists():
        print(f"error: {args.csv} not found", file=sys.stderr)
        return 1

    with args.csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    cleaned = dedupe(rows)
    dropped = len(rows) - len(cleaned)

    if args.output:
        out_path = args.output
    else:
        backup = args.csv.with_suffix(args.csv.suffix + ".bak")
        args.csv.replace(backup)
        print(f"backup: {backup.name}")
        out_path = args.csv

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned)

    print(f"input:  {len(rows)} rows")
    print(f"output: {len(cleaned)} rows ({dropped} dropped) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
