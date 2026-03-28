"""
OLX Scraper — Search Advisor

Probes OLX with real queries to measure result counts,
then uses the LLM to suggest concrete search improvements.
"""

import asyncio
import json
import logging
import re
from typing import Optional

from scraper import scrape_olx_page
from url_builder import product_to_url, category_browse_url
from llm import ask_llm
from config import CONFIG
import db

logger = logging.getLogger(__name__)

# Neighboring voivodeships for expansion suggestions
_NEIGHBORS: dict[str, list[str]] = {
    "pomorskie": ["zachodniopomorskie", "kujawsko-pomorskie", "warminsko-mazurskie"],
    "mazowieckie": ["lodzkie", "lubelskie", "podlaskie", "warminsko-mazurskie"],
    "slaskie": ["malopolskie", "opolskie", "lodzkie", "swietokrzyskie"],
    "malopolskie": ["slaskie", "podkarpackie", "swietokrzyskie"],
    "dolnoslaskie": ["opolskie", "lubuskie", "wielkopolskie"],
    "wielkopolskie": ["dolnoslaskie", "lubuskie", "kujawsko-pomorskie", "lodzkie"],
    "zachodniopomorskie": ["pomorskie", "lubuskie", "wielkopolskie"],
    "lodzkie": ["mazowieckie", "slaskie", "wielkopolskie", "swietokrzyskie"],
    "kujawsko-pomorskie": ["pomorskie", "wielkopolskie", "warminsko-mazurskie"],
    "lubelskie": ["mazowieckie", "podkarpackie", "podlaskie", "swietokrzyskie"],
    "podkarpackie": ["malopolskie", "lubelskie", "swietokrzyskie"],
    "podlaskie": ["mazowieckie", "lubelskie", "warminsko-mazurskie"],
    "warminsko-mazurskie": ["pomorskie", "mazowieckie", "podlaskie", "kujawsko-pomorskie"],
    "opolskie": ["slaskie", "dolnoslaskie", "wielkopolskie"],
    "lubuskie": ["dolnoslaskie", "zachodniopomorskie", "wielkopolskie"],
    "swietokrzyskie": ["slaskie", "malopolskie", "lodzkie", "lubelskie", "podkarpackie"],
}

# City agglomeration → voivodeship for expansion
_CITY_TO_VOIVODESHIP: dict[str, str] = {
    "gdansk": "pomorskie", "katowice": "slaskie", "warszawa": "mazowieckie",
    "krakow": "malopolskie", "wroclaw": "dolnoslaskie", "poznan": "wielkopolskie",
    "lodz": "lodzkie", "szczecin": "zachodniopomorskie", "lublin": "lubelskie",
    "bydgoszcz": "kujawsko-pomorskie", "torun": "kujawsko-pomorskie",
    "rzeszow": "podkarpackie", "bialystok": "podlaskie", "olsztyn": "warminsko-mazurskie",
    "opole": "opolskie", "kielce": "swietokrzyskie",
}


def _count_results(url: str) -> dict:
    """Scrape a URL and return local/extended/total counts."""
    try:
        listings = scrape_olx_page(url)
    except Exception as e:
        logger.warning(f"Advisor probe failed for {url}: {e}")
        return {"total": 0, "local": 0, "extended": 0, "error": str(e)}

    local = [l for l in listings if "extended_search" not in l.get("url", "")]
    return {
        "total": len(listings),
        "local": len(local),
        "extended": len(listings) - len(local),
    }


async def probe_search(search_id: int) -> dict:
    """
    Probe all URLs for a search and return coverage data.
    Returns {
        "search": {...},
        "products": [{"name": str, "url": str, "local": int, "extended": int}, ...],
        "broad": {"url": str, "local": int, ...} or None,
        "browse": {"url": str, "local": int, ...} or None,
        "total_local": int,
    }
    """
    search = db.get_search(search_id)
    if not search:
        return {"error": "Search not found"}

    urls = db.get_search_urls(search_id)
    if not urls:
        return {"error": "No URLs configured"}

    # Probe all URLs in parallel
    results = await asyncio.gather(
        *(asyncio.to_thread(_count_results, u["url"]) for u in urls)
    )

    products = []
    broad = None
    browse = None
    total_local = 0

    for url_entry, counts in zip(urls, results):
        name = url_entry.get("product_name", "")
        entry = {"name": name, "url": url_entry["url"], **counts}

        if name.startswith("[broad]"):
            broad = entry
        elif name.startswith("[browse]"):
            browse = entry
        else:
            products.append(entry)

        total_local += counts.get("local", 0)

    return {
        "search": search,
        "products": products,
        "broad": broad,
        "browse": browse,
        "total_local": total_local,
    }


async def probe_alternatives(search: dict) -> dict:
    """
    Probe alternative configurations: relaxed price, neighboring regions, nationwide.
    Returns {"relaxed_price": {...}, "neighbors": [...], "nationwide": {...}}
    """
    location = search.get("location")
    max_price = search.get("max_price")
    base_path = search.get("base_path") or "oferty"
    products_json = search.get("products")
    products = json.loads(products_json) if products_json else []
    broad_keyword = search.get("name", "")

    alternatives = {"relaxed_price": None, "neighbors": [], "nationwide": None}

    # Pick the broad keyword for testing
    broad_url_params = {
        "product_name": broad_keyword,
        "base_path": base_path,
        "location": location,
    }

    # 1. Relaxed price (+30%)
    if max_price:
        relaxed = int(max_price * 1.3)
        url = product_to_url(
            broad_keyword, max_price=relaxed,
            location=location, base_path=base_path,
        )
        counts = await asyncio.to_thread(_count_results, url)
        alternatives["relaxed_price"] = {
            "new_max_price": relaxed,
            "url": url,
            **counts,
        }

    # 2. Neighboring regions (if location is set)
    voivodeship = None
    if location:
        voivodeship = _CITY_TO_VOIVODESHIP.get(location, location)

    if voivodeship and voivodeship in _NEIGHBORS:
        neighbor_probes = []
        for neighbor in _NEIGHBORS[voivodeship][:3]:  # Top 3 neighbors
            url = product_to_url(
                broad_keyword, max_price=max_price,
                location=neighbor, base_path=base_path,
            )
            neighbor_probes.append((neighbor, url))

        results = await asyncio.gather(
            *(asyncio.to_thread(_count_results, url) for _, url in neighbor_probes)
        )
        for (neighbor, url), counts in zip(neighbor_probes, results):
            alternatives["neighbors"].append({
                "region": neighbor,
                "url": url,
                **counts,
            })

    # 3. Nationwide (no location)
    url = product_to_url(broad_keyword, max_price=max_price, base_path=base_path)
    counts = await asyncio.to_thread(_count_results, url)
    alternatives["nationwide"] = {"url": url, **counts}

    return alternatives


async def run_advisor_llm(search: dict, probe_data: dict, alt_data: dict) -> Optional[list]:
    """
    Feed probe results to LLM and get structured suggestions.
    Returns list of suggestions:
    [{"type": "add_product"|"remove_product"|"change_price"|"expand_location",
      "label": str, "reason": str, "value": ...}, ...]
    """
    # Build the probe summary for the LLM
    products_summary = []
    for p in probe_data.get("products", []):
        products_summary.append(f"- {p['name']}: {p['local']} local results")

    broad = probe_data.get("broad")
    browse = probe_data.get("browse")
    broad_line = f"- Broad keyword search: {broad['local']} results" if broad else ""
    browse_line = f"- Category browse: {browse['local']} results" if browse else ""

    # Alternatives
    alt_lines = []
    rp = alt_data.get("relaxed_price")
    if rp:
        alt_lines.append(f"- Price raised to {rp['new_max_price']} PLN: {rp['local']} results")
    for n in alt_data.get("neighbors", []):
        alt_lines.append(f"- Region {n['region']}: {n['local']} results")
    nw = alt_data.get("nationwide")
    if nw:
        alt_lines.append(f"- Nationwide (no location): {nw['local']} results")

    original_query = search.get("original_query") or search.get("name", "")
    max_price = search.get("max_price")
    location = search.get("location")

    prompt = (
        f"You are an OLX.pl search advisor. The user has a search that may need tuning.\n\n"
        f'Original request: "{original_query}"\n'
        f"Max price: {max_price} PLN\n"
        f"Location: {location or 'nationwide'}\n\n"
        f"**Current product coverage:**\n"
        + "\n".join(products_summary) + "\n"
        + (broad_line + "\n" if broad_line else "")
        + (browse_line + "\n" if browse_line else "")
        + f"\n**Alternatives tested:**\n"
        + "\n".join(alt_lines) + "\n\n"
        f"Based on this data, suggest improvements. For each suggestion, provide:\n"
        f'- "type": one of "add_product", "remove_product", "raise_price", "expand_location"\n'
        f'- "label": short human-readable description\n'
        f'- "reason": why this helps\n'
        f'- "value": the new product name / price / region slug\n\n'
        f"Rules:\n"
        f"- Remove products with 0 results\n"
        f"- Only suggest adding products that are likely to exist on OLX in that category\n"
        f"- Only suggest price/location changes if it meaningfully increases results\n"
        f"- Keep suggestions actionable and concrete (max 6)\n\n"
        f"Return ONLY valid JSON array:\n"
        f'[{{"type": "remove_product", "label": "Remove Kymco Agility", '
        f'"reason": "0 results in pomorskie", "value": "Kymco Agility"}}]\n'
        f"No markdown, no explanation."
    )

    response_text = await ask_llm(prompt)
    arr_match = re.search(r"\[.*\]", response_text, re.DOTALL)
    if not arr_match:
        logger.warning(f"Advisor LLM returned no JSON array: {response_text[:200]}")
        return None
    try:
        result = json.loads(arr_match.group())
        return result if isinstance(result, list) else None
    except json.JSONDecodeError:
        logger.warning(f"Advisor LLM JSON parse failed: {response_text[:200]}")
        return None


async def generate_advice(search_id: int) -> Optional[dict]:
    """
    Full advisor pipeline: probe → alternatives → LLM → suggestions.
    Returns {"search": {...}, "probe": {...}, "alternatives": {...},
             "suggestions": [...], "coverage_summary": str}
    """
    probe_data = await probe_search(search_id)
    if "error" in probe_data:
        return {"error": probe_data["error"]}

    search = probe_data["search"]
    alt_data = await probe_alternatives(search)
    suggestions = await run_advisor_llm(search, probe_data, alt_data)

    # Build coverage summary
    lines = []
    for p in probe_data.get("products", []):
        emoji = "✅" if p["local"] > 0 else "❌"
        lines.append(f"{emoji} {p['name']}: {p['local']} results")
    if probe_data.get("broad"):
        b = probe_data["broad"]
        lines.append(f"🔎 Broad: {b['local']} results")
    if probe_data.get("browse"):
        b = probe_data["browse"]
        lines.append(f"📂 Browse: {b['local']} results")

    return {
        "search": search,
        "probe": probe_data,
        "alternatives": alt_data,
        "suggestions": suggestions or [],
        "coverage_summary": "\n".join(lines),
    }
