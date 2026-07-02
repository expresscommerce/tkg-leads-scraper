"""Shared helpers: logging, regex extraction, slugs, CSV I/O."""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})"
)
FB_RE = re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9_.\-/]+", re.I)
INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/[A-Za-z0-9_.\-/]+", re.I
)
TIKTOK_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/[@A-Za-z0-9_.\-/]+", re.I
)


def get_logger(name: str = "scraper", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
                "%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


# Local-part substrings that mark a junk / no-reply mailbox.
JUNK_LOCAL_SUBSTRINGS = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "abuse@",
)

# Domains we never want — CMS / error-tracking noise and placeholders.
JUNK_DOMAINS = {
    # Wix
    "wix.com", "wixpress.com", "sentry-next.wixpress.com",
    # Other platforms
    "sentry.io", "wordpress.org", "wordpress.com",
    "squarespace.com", "godaddy.com",
    "schema.org", "w3.org", "shopify.com",
    # Placeholder / example domains
    "example.com", "example.org", "example.net",
    "domain.com", "yourdomain.com", "email.com",
    "test.com", "mail.com", "site.com",
    "sentry.wixpress.com",
}

# Image extensions sometimes caught by the email regex (filename hashes).
_BAD_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp")


def _is_junk_email(em: str) -> bool:
    if "@" not in em:
        return True
    local, _, domain = em.partition("@")
    if any(b in local for b in JUNK_LOCAL_SUBSTRINGS):
        return True
    if domain in JUNK_DOMAINS:
        return True
    # Sometimes Sentry / WiX hash IDs leak in like "abc123@sentry-next.wixpress.com"
    if "wixpress.com" in domain or "sentry" in domain:
        return True
    return False


def extract_emails(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_RE.findall(text):
        em = match.lower().strip(".,;:")
        if em.endswith(_BAD_SUFFIXES):
            continue
        if _is_junk_email(em):
            continue
        if len(em) >= 100 or em in seen:
            continue
        seen.add(em)
        out.append(em)
    return out


def extract_phones(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for a, b, c in PHONE_RE.findall(text):
        formatted = f"({a}) {b}-{c}"
        if formatted not in seen:
            seen.add(formatted)
            out.append(formatted)
    return out


def extract_facebook(text: str) -> str:
    if not text:
        return ""
    match = FB_RE.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip("/").split("?")[0]
    # Filter helper urls
    bad = ("/sharer", "/plugins", "/tr", "/dialog")
    if any(b in url for b in bad):
        return ""
    return url


def extract_instagram(text: str) -> str:
    if not text:
        return ""
    match = INSTAGRAM_RE.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip("/").split("?")[0]
    # Filter non-profile URLs and general info pages
    bad = ("/p/", "/reel/", "/tv/", "/stories/", "/developer", "/about", "/legal", "/directory")
    if any(b in url for b in bad):
        return ""
    return url


def extract_tiktok(text: str) -> str:
    if not text:
        return ""
    match = TIKTOK_RE.search(text)
    if not match:
        return ""
    url = match.group(0).rstrip("/").split("?")[0]
    # Filter helper paths
    bad = ("/share", "/embed")
    if any(b in url for b in bad):
        return ""
    return url


def domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in " ,_-":
            out.append("_")
    return "".join(out).strip("_") or "leads"


def default_filename(query: str, location: str) -> str:
    parts = [slugify(query)[:24], slugify(location.split(",")[0])[:24]]
    parts = [p for p in parts if p and p != "leads"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return "_".join(parts + [stamp]) if parts else f"leads_{stamp}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def parse_locations(value: str | list[str]) -> list[str]:
    """Accept a string (newline/comma-separated) or list and return a clean list."""
    if isinstance(value, list):
        raw = value
    else:
        # Split only on newlines / semicolons / pipes so that "Dallas, Texas"
        # stays intact. Users separate locations with newlines.
        raw = re.split(r"[\n;|]+", value or "")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        s = (item or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def join_unique(items: Iterable[str], sep: str = " | ") -> str:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return sep.join(out)


# Homoglyph mapping to normalize text
HOMOGLYPH_MAP = {
    # Cyrillic lookalikes
    '\u0430': 'a',  # а
    '\u0435': 'e',  # е
    '\u043e': 'o',  # о
    '\u0440': 'p',  # р
    '\u0441': 'c',  # с
    '\u0443': 'y',  # у
    '\u0445': 'x',  # х
    '\u0456': 'i',  # і
    '\u0455': 's',  # ѕ
    '\u0458': 'j',  # ј
    '\u0410': 'A',  # А
    '\u0412': 'B',  # В
    '\u0421': 'C',  # С
    '\u0415': 'E',  # Е
    '\u041d': 'H',  # Н
    '\u041e': 'O',  # О
    '\u0420': 'P',  # Р
    '\u0422': 'T',  # Т
    '\u0425': 'X',  # Х
    # Armenian lookalikes
    '\u057d': 'u',  # ս
    '\u0578': 'o',  # ո
    '\u0575': 'j',  # յ
    '\u0581': 'g',  # ց
    '\u0570': 'h',  # հ
    '\u056c': 'l',  # լ
    '\u0584': 'q',  # ք
    '\u0585': 'o',  # օ
    '\u0565': 'e',  # ե
    '\u0561': 'a',  # ա
    # Other common lookalikes/punctuation
    '\u2019': "'",  # ’
    '\u2018': "'",  # ‘
    '\u201c': '"',  # “
    '\u201d': '"',  # ”
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    # Map homoglyphs
    chars = [HOMOGLYPH_MAP.get(c, c) for c in text]
    return "".join(chars).lower().strip()


def is_relevant_business(name: str, category: str, query: str) -> bool:
    """Verify if a scraped business is relevant to the search query.

    Filters out obvious mismatches like pawn shops, convenience stores,
    or tire shops for junk car buyer queries, and non-yoga gyms for yoga queries.
    """
    name_normalized = normalize_text(name)
    cat_normalized = normalize_text(category)
    q_normalized = normalize_text(query)

    # 1. Yoga queries
    if "yoga" in q_normalized or "yogi" in q_normalized:
        if "yoga" in name_normalized or "yogi" in name_normalized or "yoga" in cat_normalized or "yogi" in cat_normalized:
            return True
        return False

    # 2. Garage door queries
    if "garage door" in q_normalized or "overhead door" in q_normalized:
        if "garage door" in cat_normalized:
            return True
        has_door = any(t in name_normalized or t in cat_normalized for t in ["door", "gate"])
        has_garage = any(t in name_normalized or t in cat_normalized for t in ["garage", "overhead", "rollup", "roll-up"])
        if has_door and has_garage:
            return True
        return False

    # 3. Junk car / Cash for cars / Car buyer / Scrap / Salvage / Sell car queries
    junk_car_query_terms = [
        "junk car", "cash for car", "car buyer", "sell car",
        "sell my car", "junk vehicle", "scrap car", "salvage car", "auto wrecker",
        # Added
        "buy junk car", "we buy cars", "car recycler", "pull a part", "pick a part",
        "u-pull-it", "pick n pull", "parts yard", "wrecking yard", "auto dismantler",
        "cash for clunkers", "vehicle recycler", "total loss buyer", "junk car removal",
        "scrap car removal", "car removal service", "auto recycling", "vehicle recycling"
    ]
    if any(t in q_normalized for t in junk_car_query_terms):
        # A. Whitelist (Always Keep — checked FIRST so genuine junk businesses
        #    are never blocked by coincidental keyword matches in exclusions)
        whitelist = [
            "junkyard", "junk yard", "salvage yard", "auto wrecker",
            "junk dealer", "salvage dealer", "scrap yard", "scrap metal", "auto recycler",
            "auto salvage", "salvage auto", "dismantler", "scrap car", "junk car", "junkcars",
            "cash for car", "cash 4 car", "car buyer", "used auto parts store", "auto parts recycler",
            "we buy car", "we buy junk", "webuycars", "i buy",
            "wrecking yard", "auto wrecking", "pick a part", "pull a part",
            "pick n pull", "u-pull-it", "u pull it", "self service auto",
            "vehicle recycler", "car recycling", "vehicle recycling", "auto recycling",
            "buy junk", "cash 4 junk", "junk vehicle", "scrap vehicle",
            "car dismantling", "vehicle dismantling", "end of life vehicle",
            "total loss vehicle", "used parts yard", "breakers yard",
            "car removal", "cash for scrap", "scrap car removal",
            "cash for clunkers", "auto dismantler", "car dismantler",
            "cash for junk", "we buy scrap", "junk car removal",
            "sell car for cash", "sell my car", "sell my junk", "junk my car",
            "auto buyer", "vehicle buyer", "truck buyer",
        ]
        if any(w in name_normalized or w in cat_normalized for w in whitelist):
            return True

        # B. Hard Exclusions (Definitely Irrelevant)
        hard_exclusions = [
            "chiropractor", "doctor", "physiotherapy", "accident clinic", "accident center",
            "haunted house", "jewelry", "gold buyer", "diamond buyer", "silver buyer",
            "self-storage", "sure storage", "appliance", "furniture", "mattress",
            "dumpster", "debris", "trash", "garbage", "waste management", "waste collection",
            "haul junk", "junk removal", "junk hauling", "hauling junk", "college hunks",
            "window tint", "upholstery", "welder", "welding", "charity", "consultant",
            "pawn shop", "pawnbroker", "estate sale", "auction house", "moving company",
            "metal roofing", "plumber", "electrician", "hvac", "locksmith",
            "rental car", "car rental", "parking", "valet", "car museum",
            "donation", "nonprofit", "food truck", "title company", "tag & title",
            "tag and title",
            # Grocery / Retail / Lifestyle
            "grocery", "supermarket", "farmers market", "h-e-b", "sprouts",
            "bowling", "apartment", "outlet mall", "premium outlet",
            "antique", "flea market", "thrift store", "thrift", "goodwill",
            "restaurant", "cafe", "food pantry", "pecan", "lumber",
            "safe & vault", "safe and vault", "office depot", "office supply",
            "electronics store", "landscaper", "landscaping", "asphalt", "paving",
            "demolition", "property maintenance", "home improvement", "habitat for humanity",
            "mover", "movers", "moving", "relocation",
            # E-waste / General recycling (not auto/metal)
            "e-waste", "ewaste", "aggregate", "brush collection",
            "battery store", "battery warehouse", "battery center",
            "auto parts manufacturer"
        ]
        if any(t in name_normalized or t in cat_normalized for t in hard_exclusions):
            return False

        # C. Soft Exclusions (Standard Auto Services / Dealerships)
        soft_exclusions = [
            "dealer", "dealership", "autosales", "autoplex",
            "repair", "mechanic", "service", "services", "transport", "tows",
            "parts supplier", "accessories", "detailing", "detail shop", "carwash", "car wash",
            "tire", "tires", "glass", "windshield", "transmission", "muffler", "brake", "collision", "body shop",
            "insurance", "agency", "pound", "government", "association", "corporate",
            "bmw", "toyota", "nissan", "ford", "mercedes", "mazda", "subaru", "porsche", "infiniti",
            "chevrolet", "honda", "lexus", "audi", "hyundai", "kia", "jeep", "chrysler", "dodge", "ram",
            "volvo", "cadillac", "buick", "gmc", "acura", "mitsubishi", "tesla",
            "towing company", "towing service", "roadside towing",
            "public auction", "government auction", "vehicle auction", "auto auction",
            "title company", "tag agency",
            "auto sales", "car sales", "vehicle sales",
            "parts store",
            # General recycling (not auto/metal specific)
            "recycling center", "recycling drop-off", "recycle center",
            "recycling department", "materials recovery",
            # Storage / Boats / Other
            "storage facility", "automobile storage", "boat"
        ]
        if any(t in name_normalized or t in cat_normalized for t in soft_exclusions):
            return False

        # Default: reject unknown categories to prevent data leakage
        return False

    # 4. Fallback for other queries
    STOP_WORDS = {
        "repair", "service", "services", "installation", "install", "maintenance",
        "near", "me", "in", "of", "and", "the", "a", "to", "for", "on", "with",
        "best", "top", "local", "company", "companies", "corp", "inc", "llc", "group"
    }
    q_words = [w for w in re.findall(r"\b[a-z0-9]+\b", q_normalized) if w not in STOP_WORDS]
    if not q_words:
        return True

    for w in q_words:
        if len(w) >= 4:
            if w in name_normalized or w in cat_normalized:
                return True
        else:
            name_words = re.findall(r"\b[a-z0-9]+\b", name_normalized)
            cat_words = re.findall(r"\b[a-z0-9]+\b", cat_normalized)
            if w in name_words or w in cat_words:
                return True

    return False

