"""
OLX Scraper — Post-scrape Location Filtering

OLX URL path location (/oferty/gdansk/) does NOT actually filter results.
This module provides reliable post-scrape filtering by checking each listing's
location text against allowed city names for the search's target area.

Supports Polish agglomerations (Trójmiasto, Śląsk, etc.) by mapping a main city
slug to a set of cities within the radius.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Polish agglomerations: main city slug → set of city names (Polish, lowercase)
# These cover the typical cities within a 25-40 km radius
_AGGLOMERATIONS: dict[str, set[str]] = {
    "gdansk": {
        "gdańsk", "sopot", "gdynia", "rumia", "reda", "wejherowo",
        "pruszcz gdański", "tczew", "starogard gdański", "żukowo",
        "kolbudy", "kowale", "osowa", "matarnia", "jasień",
    },
    "katowice": {
        "katowice", "sosnowiec", "gliwice", "zabrze", "bytom",
        "ruda śląska", "tychy", "dąbrowa górnicza", "chorzów",
        "jaworzno", "mysłowice", "siemianowice śląskie",
        "piekary śląskie", "tarnowskie góry", "będzin", "mikołów",
        "czeladź", "knurów", "łaziska górne",
    },
    "warszawa": {
        "warszawa", "piaseczno", "pruszków", "legionowo", "wołomin",
        "otwock", "marki", "ząbki", "zielonka", "kobyłka",
        "józefów", "konstancin-jeziorna", "łomianki", "piastów",
        "grodzisk mazowiecki", "nadarzyn", "jabłonna", "izabelin",
    },
    "krakow": {
        "kraków", "wieliczka", "niepołomice", "skawina", "zabierzów",
        "zielonki", "mogilany", "michałowice", "świątniki górne",
        "liszki", "kocmyrzów-luborzyca",
    },
    "wroclaw": {
        "wrocław", "siechnice", "kobierzyce", "kąty wrocławskie",
        "długołęka", "oborniki śląskie", "sobótka", "jelcz-laskowice",
        "oleśnica", "trzebnica",
    },
    "poznan": {
        "poznań", "luboń", "swarzędz", "komorniki", "mosina",
        "puszczykowo", "czerwonak", "suchy las", "dopiewo", "tarnowo podgórne",
        "rokietnica", "murowana goślina",
    },
    "lodz": {
        "łódź", "pabianice", "zgierz", "aleksandrów łódzki",
        "konstantynów łódzki", "rzgów", "tuszyn", "koluszki",
    },
    "szczecin": {
        "szczecin", "police", "stargard", "goleniów", "gryfino",
    },
    "lublin": {
        "lublin", "świdnik", "lubartów", "łęczna", "puławy",
    },
    "bydgoszcz": {
        "bydgoszcz", "toruń", "inowrocław", "solec kujawski",
    },
}

# Single-city slugs → city name (lowercase, with diacritics)
_CITY_SLUG_MAP: dict[str, str] = {
    "gdansk": "gdańsk",
    "katowice": "katowice",
    "warszawa": "warszawa",
    "krakow": "kraków",
    "wroclaw": "wrocław",
    "poznan": "poznań",
    "lodz": "łódź",
    "szczecin": "szczecin",
    "lublin": "lublin",
    "bydgoszcz": "bydgoszcz",
    "torun": "toruń",
    "rzeszow": "rzeszów",
    "bialystok": "białystok",
    "olsztyn": "olsztyn",
    "opole": "opole",
    "kielce": "kielce",
    "radom": "radom",
    "czestochowa": "częstochowa",
    "gorzow-wielkopolski": "gorzów wielkopolski",
    "zielona-gora": "zielona góra",
}


def _normalize(text: str) -> str:
    """Lowercase and strip whitespace."""
    return text.strip().lower()


# Polish voivodeships — OLX reliably filters by region when it's in the URL path,
# so post-scrape filtering is unnecessary and harmful (city names never match).
_VOIVODESHIPS: set[str] = {
    "dolnoslaskie", "kujawsko-pomorskie", "lubelskie", "lubuskie",
    "lodzkie", "malopolskie", "mazowieckie", "opolskie", "podkarpackie",
    "podlaskie", "pomorskie", "slaskie", "swietokrzyskie",
    "warminsko-mazurskie", "wielkopolskie", "zachodniopomorskie",
}


def get_allowed_cities(location_slug: Optional[str], location_radius: Optional[int]) -> Optional[set[str]]:
    """
    Return a set of allowed city names for the given location.
    Returns None if no location filtering should be applied.
    """
    if not location_slug:
        return None

    slug = location_slug.strip().lower()

    # Voivodeship — OLX handles region filtering in the URL; skip post-scrape filter
    if slug in _VOIVODESHIPS:
        return None

    # If radius is large enough, use agglomeration set
    if location_radius and location_radius >= 15 and slug in _AGGLOMERATIONS:
        return _AGGLOMERATIONS[slug]

    # Single city
    if slug in _CITY_SLUG_MAP:
        return {_CITY_SLUG_MAP[slug]}

    # Unknown slug — use it as-is (might still match)
    return {slug}


def filter_by_location(listings: list[dict], location_slug: Optional[str], location_radius: Optional[int]) -> list[dict]:
    """
    Filter listings by location. Each listing should have a "location" field
    like "Gdańsk, Śródmieście" or "Sopot".

    Returns the filtered list. If no location is set, returns all listings.
    """
    allowed = get_allowed_cities(location_slug, location_radius)
    if not allowed:
        return listings

    result = []
    for listing in listings:
        loc = listing.get("location", "")
        if not loc:
            # No location info — keep it (benefit of the doubt)
            result.append(listing)
            continue

        # OLX location format: "City, District" or just "City"
        city = _normalize(loc.split(",")[0])

        if city in allowed:
            result.append(listing)
        else:
            logger.debug(f"Location filter: rejected '{listing.get('title', '')[:40]}' — {loc} not in {allowed}")

    if len(result) < len(listings):
        logger.info(f"Location filter: {len(result)}/{len(listings)} passed for {location_slug}")

    return result
