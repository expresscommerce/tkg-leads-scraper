"""LinkedIn URL discovery.

Uses CloakBrowser (stealth Chromium) to search multiple search engines
(Yahoo, Google, Bing, DuckDuckGo) with fallback when rate-limited.

Extracts the first matching LinkedIn company URL and performs strict relevancy
matching to avoid unrelated business leaks.

Company size scraping is set aside (skipped) to prevent account ban risks.
"""

from __future__ import annotations

import base64
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from .config import settings
from .maps import Business
from .utils import domain as get_domain, get_logger

logger = get_logger("scraper.linkedin")

_LINKEDIN_CO_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?linkedin\.com/company/([A-Za-z0-9_-]+)",
    re.I,
)

# Domains that are generic / social and should NOT be used for domain-based search
_GENERIC_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "youtube.com", "linkedin.com",
    "wixsite.com", "squarespace.com", "wordpress.com", "weebly.com", "linktr.ee",
    "google.com", "yelp.com", "tripadvisor.com", "tiktok.com", "pinterest.com",
}


def clean_linkedin_url(url: str) -> str:
    """Extract and normalize a clean LinkedIn company URL."""
    m = _LINKEDIN_CO_RE.search(url)
    if m:
        slug = m.group(1).lower()
        # Skip generic slugs that are just LinkedIn navigation
        if slug in ("company", "companies", "login", "signup"):
            return ""
        return f"https://www.linkedin.com/company/{slug}"
    return ""


def clean_query_name(name: str) -> str:
    """Clean the business name for a higher match rate by removing suffixes and structures."""
    # Remove common geographical suffixes and company structures
    name = re.sub(
        r"\b(of\s+)?(Houston|Dallas|Austin|San Antonio|Fort Worth|El Paso|Arlington|Corpus Christi|Plano|Lubbock|Laredo|Garland|Irving|Amarillo|Grand Prairie|McKinney|Frisco|Brownsville|Pasadena|Killeen|McAllen|Mesquite|Midland|Denton|Carrollton|Waco|Round Rock|Abilene|Pearland|Richardson|Odessa|Sugar Land|Beaumont|The Woodlands|College Station|Lewisville|Tyler|Allen|League City|San Angelo|TX)\b",
        "",
        name,
        flags=re.I,
    )
    name = re.sub(
        r"\b(LLC|Inc|Corp|Co|LTD|Corporation|Limited)\b\.?",
        "",
        name,
        flags=re.I,
    )
    # Remove extra spaces/dashes
    name = re.sub(r"\s*-\s*", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def decode_redirect_url(url: str) -> str:
    """Extract and decode target URLs from search engine redirect wrappers."""
    if not url:
        return ""
    
    # 1. DuckDuckGo redirect: uddg=...
    if "uddg=" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                return params["uddg"][0]
        except Exception:
            pass

    # 2. Yahoo redirect: RU=... or /RU=...
    if "r.search.yahoo.com" in url or "/RU=" in url:
        try:
            m = re.search(r"/RU=([^/]+)", url)
            if m:
                return urllib.parse.unquote(m.group(1))
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "RU" in params:
                return params["RU"][0]
        except Exception:
            pass

    # 3. Bing redirect: u=... (base64 encoded after a prefix)
    if "bing.com/ck/a" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "u" in params:
                u_val = params["u"][0]
                idx = u_val.find("aHR0")
                if idx != -1:
                    b64_str = u_val[idx:]
                    b64_str = b64_str.replace("-", "+").replace("_", "/")
                    padding = len(b64_str) % 4
                    if padding:
                        b64_str += "=" * (4 - padding)
                    decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
                    return decoded
        except Exception:
            pass

    # 4. Google redirect: /url?q=...
    if "/url?q=" in url or "google.com/url?q=" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "q" in params:
                return params["q"][0]
        except Exception:
            pass

    return url


def is_relevant_linkedin(
    biz_name: str,
    domain_val: str,
    linkedin_url: str,
    result_title: str,
    result_snippet: str,
) -> bool:
    """Verify if a found LinkedIn URL is relevant to the target business.
    
    Prevents unrelated business matches or false positives.
    """
    biz_name_clean = clean_query_name(biz_name).lower()
    result_title = result_title.lower()
    result_snippet = result_snippet.lower()
    
    m = _LINKEDIN_CO_RE.search(linkedin_url)
    if not m:
        return False
    slug = m.group(1).lower()
    
    # Rule 1: Match by domain (highest confidence)
    if domain_val:
        core_domain = domain_val.split('.')[0].lower()
        if core_domain in slug or core_domain in result_title or core_domain in result_snippet:
            return True
            
    # Clean company name into individual words
    words = [w for w in re.split(r'\W+', biz_name_clean) if len(w) > 2]
    if not words:
        words = [w for w in re.split(r'\W+', biz_name_clean) if w]
        
    # Rule 2: Complete name match in slug or result title
    if all(w in slug for w in words):
        return True
        
    if all(w in result_title for w in words):
        return True

    # Rule 3: Fuzzy overlap match on slug words (>= 50% overlap of name words)
    slug_words = re.split(r'\W+|_|-', slug)
    matching_words = sum(1 for w in words if w in slug_words)
    if matching_words >= len(words) * 0.5:
        return True
        
    return False


def _build_query(biz: Business) -> str:
    """Build the best search query for a business."""
    name = clean_query_name(biz.name)
    domain_val = get_domain(biz.website) if biz.website else ""

    if domain_val and domain_val not in _GENERIC_DOMAINS:
        # Approach A: Search by unique domain
        return f'"{domain_val}" site:linkedin.com/company'
    else:
        # Approach B: Search by company name + optional city/state
        city_state = ""
        if biz.address:
            m = re.search(r",\s*([^,]+),\s*([A-Z]{2})\b", biz.address)
            if m:
                city_state = f"{m.group(1).strip()} {m.group(2).strip()}"

        if city_state:
            return f'"{name}" "{city_state}" site:linkedin.com/company'
        else:
            return f'"{name}" site:linkedin.com/company'


def _is_blocked(page, engine_name: str) -> bool:
    """Check if the search engine page returned a CAPTCHA / rate-limit challenge."""
    try:
        text = page.inner_text("body").lower()
        if engine_name == "google":
            return "unusual traffic" in text or "captcha" in text
        elif engine_name == "yahoo":
            return "unusual traffic" in text or "captcha" in text or "please verify" in text
        elif engine_name == "bing":
            return "unusual traffic" in text or "captcha" in text or "verify you are" in text or "challenge" in text
        elif engine_name == "duckduckgo":
            return "challenge to confirm" in text or "captcha" in text or "ddg-captcha" in text
    except Exception:
        pass
    return False


def _extract_linkedin_from_page(page, biz: Business, engine_name: str) -> str:
    """Parse search results page, find all result blocks, and return the first relevant LinkedIn URL."""
    biz_name = biz.name
    domain_val = get_domain(biz.website) if biz.website else ""
    
    # 1. Try to extract structured blocks based on engine
    blocks = []
    if engine_name == "yahoo":
        blocks = page.locator("div.dd.algo, li.b_algo, div.algo, li div.compTitle").all()
    elif engine_name == "google":
        blocks = page.locator("div.g, div.logo, div.v7wdu, div.kb0PBd").all()
    elif engine_name == "bing":
        blocks = page.locator("li.b_algo").all()
    elif engine_name == "duckduckgo":
        blocks = page.locator("div.result, div.web-result").all()
        
    for block in blocks:
        try:
            for a in block.locator("a").all():
                href = a.get_attribute("href") or ""
                decoded_href = decode_redirect_url(href)
                cleaned = clean_linkedin_url(decoded_href)
                if cleaned:
                    title = a.inner_text() or ""
                    snippet = block.inner_text() or ""
                    if is_relevant_linkedin(biz_name, domain_val, cleaned, title, snippet):
                        return cleaned
        except Exception:
            pass

    # 2. Fallback: check all <a> links on the page directly
    for a in page.locator("a").all():
        try:
            href = a.get_attribute("href") or ""
            decoded_href = decode_redirect_url(href)
            cleaned = clean_linkedin_url(decoded_href)
            if cleaned:
                title = a.inner_text() or ""
                if is_relevant_linkedin(biz_name, domain_val, cleaned, title, ""):
                    return cleaned
        except Exception:
            pass

    return ""


def find_linkedin_urls(
    businesses: list[Business],
    *,
    progress=None,
    headless: bool = True,
    min_delay: float = 2.0,
    max_delay: float = 4.0,
) -> None:
    """Discover LinkedIn company URLs via multi-engine search using CloakBrowser.

    Utilizes Yahoo, Google, Bing, and DuckDuckGo in sequence when blocked.
    Mutates `Business.linkedin_url` in place.
    """
    targets = [b for b in businesses if not b.linkedin_url and b.name]
    if not targets:
        logger.info("No businesses need LinkedIn URL discovery.")
        return

    # pyrefly: ignore [missing-import]
    from cloakbrowser import launch

    total = len(targets)
    logger.info("Starting LinkedIn URL discovery for %d businesses...", total)

    # Search engine sequences
    engines = ["yahoo", "google", "bing", "duckduckgo"]
    engine_idx = 0

    browser = launch(headless=headless, humanize=True)
    page = browser.new_page()

    try:
        for idx, biz in enumerate(targets, 1):
            query = _build_query(biz)
            linkedin_url = ""
            
            # Keep trying engines in sequence if one is blocked
            while engine_idx < len(engines):
                current_engine = engines[engine_idx]
                
                # Format URL based on current engine
                if current_engine == "yahoo":
                    url = f"https://search.yahoo.com/search?p={urllib.parse.quote(query)}"
                elif current_engine == "google":
                    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=en&gl=us"
                elif current_engine == "bing":
                    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&setLang=en&cc=US&mkt=en-US"
                else:  # duckduckgo
                    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(2500)
                    
                    if _is_blocked(page, current_engine):
                        logger.warning(
                            "Search engine '%s' blocked/rate-limited. Switching to next engine.",
                            current_engine
                        )
                        engine_idx += 1
                        continue  # Try next engine on the same query
                        
                    linkedin_url = _extract_linkedin_from_page(page, biz, current_engine)
                    break  # Succeeded or got no results; move to next business
                except Exception as exc:
                    logger.debug(
                        "Search failed on engine '%s' for %s: %s",
                        current_engine, biz.name, exc
                    )
                    engine_idx += 1  # Fallback to next engine on error

            if linkedin_url:
                biz.linkedin_url = linkedin_url
                logger.info(
                    "[%d/%d] Found LinkedIn URL for %s → %s",
                    idx, total, biz.name, linkedin_url,
                )
            else:
                logger.debug(
                    "[%d/%d] No LinkedIn URL found for %s",
                    idx, total, biz.name,
                )

            if progress:
                progress(
                    "linkedin_url",
                    idx,
                    total,
                    f"LinkedIn URL {idx}/{total}: {biz.name[:35]}",
                )

            # Polite delay between searches to prevent rate limits
            if idx < total:
                time.sleep(random.uniform(min_delay, max_delay))

    finally:
        browser.close()

    found = sum(1 for b in businesses if b.linkedin_url)
    logger.info("LinkedIn URL discovery complete. Found: %d / %d", found, len(businesses))


def enrich_linkedin(
    businesses: list[Business],
    *,
    progress=None,
    skip_size: bool = True,  # Company size is skipped/set aside
    session_path: Optional[Path] = None,
    headless: bool = True,
) -> None:
    """Combined entry point for the pipeline. Focuses solely on finding LinkedIn URLs."""
    find_linkedin_urls(businesses, progress=progress, headless=headless)
