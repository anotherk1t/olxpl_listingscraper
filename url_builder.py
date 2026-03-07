"""
OLX Scraper — URL Building & Validation

Assemble, validate, and self-correct OLX search URLs.
Uses cached categories from config to avoid re-reading files.
"""

import json
import logging
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from config import CONFIG, HTTP_HEADERS, OLX_CATEGORIES, OLX_URL_CONTEXT
from llm import ask_gemini
from scraper import _parse_json_ld, _parse_html_cards

logger = logging.getLogger(__name__)


def assemble_url(
    base_path: str, keyword: str, max_price=None, condition: str = None,
    location: str = None, location_radius: int = None, min_price=None,
) -> str:
    """Build a clean OLX search URL from components."""
    slug = keyword.strip().lower().replace(" ", "-")
    path = base_path.strip("/")
    if location:
        url = f"https://www.olx.pl/{path}/{location.strip('/')}/q-{slug}/"
    else:
        url = f"https://www.olx.pl/{path}/q-{slug}/"
    params = {}
    if min_price is not None:
        try:
            params["search[filter_float_price:from]"] = int(float(min_price))
        except (ValueError, TypeError):
            pass
    if max_price:
        try:
            params["search[filter_float_price:to]"] = int(float(max_price))
        except (ValueError, TypeError):
            pass
    if condition in ("new", "used"):
        params["search[filter_enum_state][0]"] = condition
    if location_radius:
        params["search[dist]"] = int(location_radius)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def product_to_url(
    product_name: str, max_price=None,
    location: str = None, location_radius: int = None,
    base_path: str = None, condition: str = None,
    min_price=None, custom_filters: dict = None,
) -> str:
    """Convert a product model name to an OLX search URL.
    
    base_path: OLX category path (e.g. 'elektronika/komputery'). Defaults to 'oferty'.
    condition: 'new' or 'used' — adds state filter param.
    custom_filters: extra OLX search params, e.g. {"enginesize:to": 125, "year:from": 2010}.
    """
    slug = re.sub(r"[^a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ\s]", "", product_name)
    slug = re.sub(r"\s+", "-", slug.strip()).lower()
    path = (base_path or "oferty").strip("/")
    if location:
        url = f"{CONFIG.OLX_BASE_URL}/{path}/{location.strip('/')}/q-{slug}/"
    else:
        url = f"{CONFIG.OLX_BASE_URL}/{path}/q-{slug}/"
    params = {}
    if min_price is not None:
        params["search[filter_float_price:from]"] = int(min_price)
    if max_price is not None:
        params["search[filter_float_price:to]"] = int(max_price)
    if condition in ("new", "used"):
        params["search[filter_enum_state][0]"] = condition
    if location_radius:
        params["search[dist]"] = int(location_radius)
    if custom_filters:
        for key, val in custom_filters.items():
            if ":" in key:
                params[f"search[filter_float_{key}]"] = int(val)
            else:
                params[f"search[filter_enum_{key}][0]"] = val
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def category_browse_url(
    category_path: str, max_price=None, min_price=None,
    condition: str = None, location: str = None, location_radius: int = None,
    custom_filters: dict = None,
) -> str:
    """Build a keyword-less OLX browse URL for a leaf category, sorted by newest.

    This catches generic-title listings that keyword searches miss.
    """
    path = category_path.strip("/")
    if location:
        url = f"{CONFIG.OLX_BASE_URL}/{path}/{location.strip('/')}/"
    else:
        url = f"{CONFIG.OLX_BASE_URL}/{path}/"
    params = {"search[order]": "created_at:desc"}
    if min_price is not None:
        try:
            params["search[filter_float_price:from]"] = int(float(min_price))
        except (ValueError, TypeError):
            pass
    if max_price is not None:
        try:
            params["search[filter_float_price:to]"] = int(float(max_price))
        except (ValueError, TypeError):
            pass
    if condition in ("new", "used"):
        params["search[filter_enum_state][0]"] = condition
    if location_radius:
        params["search[dist]"] = int(location_radius)
    if custom_filters:
        for key, val in custom_filters.items():
            if ":" in key:
                params[f"search[filter_float_{key}]"] = int(val)
            else:
                params[f"search[filter_enum_{key}][0]"] = val
    url += "?" + urllib.parse.urlencode(params)
    return url


async def validate_and_correct_url(
    url: str,
    keyword: str,
    search_context: dict,
    max_retries: int = 3,
) -> tuple[str, bool]:
    """
    Validate a generated OLX URL by actually fetching it.
    Returns (final_url, used_fallback).

    Tier 1: structural check (base path must be in OLX_CATEGORIES).
    Tier 2: live fetch check (HTTP 200 + ≥1 listing). On failure the LLM
            gets the error and proposes a corrected URL (up to max_retries).
    Falls back to a broad keyword URL if all attempts fail.
    """
    categories = OLX_CATEGORIES

    # Tier 1: structural check
    parsed = urllib.parse.urlparse(url)
    base_path = parsed.path.strip("/").split("/q-")[0]
    if base_path.strip("/") not in categories:
        logger.warning(f"URL structural check failed for base_path '{base_path}', skipping to live check")

    # Tier 2: live fetch + LLM self-correction loop
    current_url = url
    for attempt in range(max_retries):
        try:
            resp = requests.get(current_url, headers=HTTP_HEADERS, timeout=CONFIG.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                listings = _parse_json_ld(soup)
                if not listings:
                    listings = _parse_html_cards(soup)
                if listings:
                    logger.info(f"URL validated on attempt {attempt + 1}: {current_url}")
                    return current_url, False
                error_desc = f"HTTP 200 but 0 listings found at {current_url}"
            else:
                error_desc = f"HTTP {resp.status_code} for {current_url}"
        except requests.RequestException as e:
            error_desc = f"Request error: {e}"

        logger.warning(f"URL validation attempt {attempt + 1} failed: {error_desc}")

        fix_prompt = (
            f"OLX URL failed: {current_url} → {error_desc}\n"
            f"Keyword: {keyword}, max_price: {search_context.get('max_price')}\n"
            f"Pick a base path from: {json.dumps(categories)}\n"
            f"URL format: https://www.olx.pl/{{base_path}}/q-{{keyword}}/\n"
            f"Reply with ONLY the corrected URL, nothing else."
        )

        corrected = (await ask_gemini(fix_prompt)).strip()
        corrected = re.sub(r"^```.*\n?", "", corrected, flags=re.MULTILINE).strip()
        if corrected.startswith("https://www.olx.pl"):
            current_url = corrected
        else:
            logger.warning(f"LLM correction did not return a valid URL: {corrected!r}")

    # Fallback
    slug = keyword.strip().lower().replace(" ", "-")
    fallback = f"https://www.olx.pl/oferty/q-{slug}/"
    logger.warning(f"All URL validation attempts failed. Using fallback: {fallback}")
    return fallback, True
