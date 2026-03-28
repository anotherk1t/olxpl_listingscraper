"""
OLX Scraper — Web Scraping

Fetch and parse OLX.pl search pages. Supports JSON-LD
structured data with an HTML card fallback.
"""

import json
import logging
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

from config import CONFIG, HTTP_HEADERS

logger = logging.getLogger(__name__)

# Schema.org condition URIs → human-readable labels
_CONDITION_MAP = {
    "NewCondition": "New",
    "UsedCondition": "Used",
    "RefurbishedCondition": "Refurbished",
    "DamagedCondition": "Damaged",
}


# ============================================================================
# PARSING
# ============================================================================


def _parse_json_ld(soup: BeautifulSoup) -> list[dict]:
    """Parse listings from JSON-LD structured data."""
    script = soup.find("script", {"type": "application/ld+json"})
    if not script or not script.string:
        return []

    try:
        data = json.loads(script.string)
        offers = data.get("offers", {}).get("offers", [])

        listings = []
        for offer in offers:
            url = offer.get("url")
            if not url:
                continue

            match = re.search(r"-ID([a-zA-Z0-9]+)\.html", url)
            listing_id = match.group(1) if match else url.split("/")[-1]

            listings.append(
                {
                    "id": listing_id,
                    "title": re.sub(r"\s+", " ", offer.get("name", "")).strip(),
                    "price": f"{offer.get('price', 0)} {offer.get('priceCurrency', 'PLN')}",
                    "url": url,
                }
            )

        return listings
    except (json.JSONDecodeError, AttributeError):
        return []


def _parse_html_cards(soup: BeautifulSoup) -> list[dict]:
    """Parse listings from HTML cards (fallback method)."""
    cards = soup.find_all("div", {"data-cy": "l-card"})
    listings = []

    for card in cards:
        try:
            listing_id = card.get("id")
            if not listing_id:
                continue

            title_el = card.find("h4")
            price_el = card.find("p", {"data-testid": "ad-price"})
            link_el = card.find("a", href=True)

            if not link_el:
                continue

            href = link_el["href"]
            url = href if href.startswith("http") else f"{CONFIG.OLX_BASE_URL}{href}"

            # Extract location from "City, District - date" text
            loc_el = card.find("p", {"data-testid": "location-date"})
            location = ""
            if loc_el:
                loc_text = loc_el.text.strip()
                # Format: "Gdańsk, Śródmieście - 17 lutego 2026"
                location = loc_text.split(" - ")[0].strip() if " - " in loc_text else loc_text

            listings.append(
                {
                    "id": listing_id,
                    "title": title_el.text.strip() if title_el else "No Title",
                    "price": price_el.text.strip() if price_el else "No Price",
                    "url": url,
                    "location": location,
                }
            )
        except Exception as e:
            logger.debug(f"Error parsing card: {e}")

    return listings


# ============================================================================
# SCRAPING
# ============================================================================


def scrape_olx_page(url: str) -> list[dict]:
    """Scrape a single OLX search page for listings."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=CONFIG.REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "lxml")

    # Try JSON-LD first, fall back to HTML parsing
    listings = _parse_json_ld(soup)
    if listings:
        logger.debug(f"Found {len(listings)} listings via JSON-LD")
        # JSON-LD lacks location — enrich from HTML cards
        html_cards = _parse_html_cards(soup)
        loc_map = {c["id"]: c.get("location", "") for c in html_cards}
        for listing in listings:
            if "location" not in listing or not listing.get("location"):
                listing["location"] = loc_map.get(listing["id"], "")
    else:
        listings = _parse_html_cards(soup)
        logger.debug(f"Found {len(listings)} listings via HTML fallback")

    # Discard cross-site results (otomoto.pl, etc.) that OLX mixes in
    listings = [l for l in listings if "olx.pl" in l.get("url", "")]
    return listings


def scrape_olx(base_url: str, paginate: bool = False) -> list[dict]:
    """Scrape OLX search results, optionally paginating through multiple pages."""
    if not paginate:
        return scrape_olx_page(base_url)

    all_listings = []
    seen_ids: set[str] = set()
    current_page = 1

    parsed_url = urllib.parse.urlparse(base_url)
    query_params = urllib.parse.parse_qs(parsed_url.query)

    while current_page <= CONFIG.MAX_SCRAPE_PAGES:
        query_params["page"] = [str(current_page)]

        new_query = urllib.parse.urlencode(query_params, doseq=True)
        page_url = urllib.parse.urlunparse(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment,
            )
        )

        logger.info(f"Scraping page {current_page}: {page_url}")
        page_listings = scrape_olx_page(page_url)

        if not page_listings:
            logger.info("No listings found on this page. Stopping pagination.")
            break

        new_found = False
        for listing in page_listings:
            if listing["id"] not in seen_ids:
                seen_ids.add(listing["id"])
                all_listings.append(listing)
                new_found = True

        if not new_found:
            logger.info("No new listings on this page (likely looping). Stopping.")
            break

        current_page += 1
        time.sleep(CONFIG.PAGE_SCRAPE_DELAY)

    return all_listings


# ============================================================================
# LISTING DETAIL FETCH
# ============================================================================


def fetch_listing_details(url: str) -> dict:
    """Fetch a single OLX listing page and return description, condition, and location."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=CONFIG.REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        script = soup.find("script", {"type": "application/ld+json"})
        if not script or not script.string:
            return {}
        data = json.loads(script.string)
        description = data.get("description", "").strip()
        offers = data.get("offers", {})
        condition_uri = offers.get("itemCondition", "")
        condition_key = condition_uri.split("/")[-1] if condition_uri else ""
        condition = _CONDITION_MAP.get(condition_key, "")
        location = offers.get("areaServed", {}).get("name", "") if isinstance(offers.get("areaServed"), dict) else ""
        return {"description": description, "condition": condition, "location": location}
    except Exception as e:
        logger.debug(f"Could not fetch listing details from {url}: {e}")
        return {}
