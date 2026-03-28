"""
OLX Scraper — LLM Integration

All LLM calls go through the Copilot CLI proxy (Node.js sidecar on the host).
"""

import asyncio
import json
import logging
import re
from typing import Optional

import requests as _requests

from config import CONFIG, LLM_PROXY_URL

logger = logging.getLogger(__name__)


# ============================================================================
# COPILOT CLI PROXY CALL
# ============================================================================

def _ask_llm_sync(
    prompt: str,
    *,
    model: str = None,
    mcp: bool = False,
    timeout: int = 120,
) -> str:
    """Synchronous Copilot proxy call (runs in thread via ask_llm)."""
    try:
        body = {"prompt": prompt}
        if model:
            body["model"] = model
        if mcp:
            body["mcp"] = True
        resp = _requests.post(
            LLM_PROXY_URL,
            json=body,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            logger.warning(f"LLM proxy warning: {data['error']}")
        return data.get("response", "")
    except _requests.exceptions.ConnectionError:
        logger.error(f"LLM proxy unreachable at {LLM_PROXY_URL}")
        return ""
    except _requests.exceptions.Timeout:
        logger.error("LLM proxy request timed out")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error calling LLM proxy: {e}")
        return ""


async def ask_llm(
    prompt: str,
    *,
    model: str = None,
    mcp: bool = False,
    timeout: int = 120,
) -> str:
    """Non-blocking Copilot proxy call — runs sync HTTP in a thread."""
    return await asyncio.to_thread(
        _ask_llm_sync,
        prompt,
        model=model,
        mcp=mcp,
        timeout=timeout,
    )


# ============================================================================
# BATCH LLM FILTER (slopsearch two-stage)
# ============================================================================

async def _llm_batch_call(batch: list, kw_str: str, with_details: bool) -> list:
    """Send one batch to the LLM, return items that pass filtering."""
    lines = []
    for j, item in enumerate(batch):
        if with_details:
            cond = f" | Condition: {item['condition']}" if item.get("condition") else ""
            desc = item.get("description", "")
            desc_part = f" | {desc[:120]}" if desc else ""
            lines.append(f"{j+1}. [{item['price']}] {item['title']}{cond}{desc_part}")
        else:
            lines.append(f"{j+1}. [{item['price']}] {item['title']}")

    prompt = (
        f"You are filtering OLX.pl listings for a buyer.\n"
        f"Requirements: {kw_str}\n\n"
        f"Listings:\n" + "\n".join(lines) + "\n\n"
        f"Reply with ONLY a comma-separated list of the numbers that match the requirements "
        f"(e.g. '1,3,5'). If none match, reply with '0'. No explanation."
    )
    response = (await ask_llm(prompt)).strip()
    if response == "0" or not response:
        return []
    try:
        indices = [int(x.strip()) - 1 for x in response.split(",") if x.strip().isdigit()]
        return [batch[i] for i in indices if 0 <= i < len(batch)]
    except ValueError:
        logger.warning(f"Unexpected LLM batch response: {response!r}")
        return []


async def batch_llm_filter(listings: list, keywords: list) -> list:
    """
    Two-stage LLM filter:
      Stage 1 — title+price only (fast). Eliminates obvious mismatches.
      Stage 2 — fetch description+condition for survivors, refine.
    Returns items that pass both stages.
    """
    import concurrent.futures
    from scraper import fetch_listing_details

    if not listings:
        return []
    if not keywords:
        return listings

    kw_str = ", ".join(keywords)
    batch_size = CONFIG.BATCH_SIZE

    # Stage 1: title-only filter
    stage1_passed = []
    for i in range(0, len(listings), batch_size):
        batch = listings[i : i + batch_size]
        stage1_passed.extend(await _llm_batch_call(batch, kw_str, with_details=False))

    logger.info(f"Stage 1 (title filter): {len(stage1_passed)}/{len(listings)} passed")
    if not stage1_passed:
        return []

    # Stage 2: fetch details in thread pool (blocking I/O)
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG.DETAIL_WORKERS) as executor:
        futures = {
            item["url"]: loop.run_in_executor(executor, fetch_listing_details, item["url"])
            for item in stage1_passed
        }
        for item in stage1_passed:
            item.update(await futures[item["url"]])

    stage2_passed = []
    for i in range(0, len(stage1_passed), batch_size):
        batch = stage1_passed[i : i + batch_size]
        stage2_passed.extend(await _llm_batch_call(batch, kw_str, with_details=True))

    logger.info(
        f"Stage 2 (detail filter): {len(stage2_passed)}/{len(stage1_passed)} passed "
        f"→ {len(stage2_passed)}/{len(listings)} total"
    )
    return stage2_passed


# ============================================================================
# CHEAP MODE SUMMARIES
# ============================================================================

async def get_cheap_summaries(
    listings: list, details_list: list, original_query: str, product: str,
) -> list[dict]:
    """
    Call Gemini once for a product batch.
    Returns list of {"pass": bool, "summary": str} dicts (one per listing).
    pass=False means the listing doesn't match the user's query and should be skipped.
    summary is a 2-7 word assessment.
    """
    if not listings:
        return []
    lines = []
    for i, (listing, det) in enumerate(zip(listings, details_list)):
        cond = det.get("condition", "") or "unknown"
        desc = (det.get("description", "") or "")[:180]
        lines.append(f"{i+1}. {listing['title']} | {listing['price']} | {cond} | {desc}")

    is_broad_or_browse = product.startswith("[broad]") or product.startswith("[browse]")
    strictness = (
        "BE VERY STRICT. This is a broad category search, so most listings will NOT match. "
        "Only pass listings that clearly match ALL of the user's specific requirements "
        "(brand, type, features, condition). When in doubt, reject.\n"
        if is_broad_or_browse else ""
    )

    prompt = (
        f'User query: "{original_query}"\n'
        f"Product model searched: {product}\n\n"
        f"{strictness}"
        f"For each listing below, decide if it matches the user's query.\n"
        f"Reply with ONLY a JSON array of {len(listings)} objects, one per listing, same order:\n"
        f'[{{"pass": true, "summary": "2-7 word assessment"}}, ...]\n'
        f"Set pass=false if the listing is clearly irrelevant, wrong product, wrong brand, "
        f"doesn't fit the query requirements, or is a no-name/unknown brand when user asked for known brands.\n\n"
        + "\n".join(lines)
    )
    try:
        resp = (await ask_llm(prompt)).strip()
        match = re.search(r"\[.*\]", resp, re.DOTALL)
        if match:
            results = json.loads(match.group())
            if isinstance(results, list) and len(results) == len(listings):
                return [
                    {"pass": bool(r.get("pass", True)), "summary": str(r.get("summary", ""))}
                    for r in results
                ]
            logger.warning(
                f"Cheap summaries: LLM returned {len(results) if isinstance(results, list) else 'non-list'} "
                f"results for {len(listings)} listings (product: {product})"
            )
        else:
            logger.warning(f"Cheap summaries: no JSON array found in LLM response for '{product}'")
    except Exception as e:
        logger.warning(f"Cheap summaries LLM call failed: {e}")
    # Fallback: reject everything for broad/browse (too risky to pass), pass for specific products
    if is_broad_or_browse:
        logger.warning(f"Cheap summaries fallback: rejecting all {len(listings)} broad/browse listings for '{product}'")
        return [{"pass": False, "summary": "filter error"}] * len(listings)
    return [{"pass": True, "summary": ""}] * len(listings)


# ============================================================================
# SLOPSEARCH LLM (NL query → structured search dict)
# ============================================================================

async def run_slopsearch_llm(
    user_query: str,
    categories: list,
    url_context: str,
    existing: dict = None,
) -> Optional[dict]:
    """
    Turn a natural language query into a structured search dict.
    If `existing` is provided, the LLM refines/modifies it.
    Returns dict with name, base_path, keyword, max_price, condition, keywords or None.
    """
    if existing:
        modification_instructions = (
            f"You are updating an existing slopsearch. The current fields are:\n"
            f"{json.dumps(existing, indent=2, ensure_ascii=False)}\n\n"
            f'The user wants to modify it with: "{user_query}"\n\n'
            f"Update only the relevant fields based on the modification request. Keep unchanged fields as-is."
        )
    else:
        modification_instructions = f'User request: "{user_query}"'

    prompt = (
        f"You are an OLX.pl search assistant. {modification_instructions}\n\n"
        f"Return a JSON object with these fields:\n"
        f'- "name": short search label (1-3 words, Polish or English)\n'
        f'- "base_path": the OLX category path. You MUST pick EXACTLY one value from this list:\n'
        f"{json.dumps(categories, indent=2)}\n"
        f'- "keyword": a single broad Polish search term (e.g. "laptop", "myszka"). No brand names unless essential.\n'
        f'- "max_price": numeric max price in PLN extracted from the request, or null\n'
        f'- "min_price": numeric min price in PLN if the user specifies a price range (e.g. "200-500 PLN" → min_price=200, max_price=500), or null\n'
        f'- "condition": "new", "used", or null\n'
        f'- "keywords": list of specific requirements to evaluate listing-by-listing '
        f'(e.g. ["Thinkpad T480", "8GB RAM", "working battery"])\n'
        f'- "location": OLX city slug (lowercase, no diacritics, e.g. "gdansk", "warszawa") or null\n'
        f'- "location_radius": search radius in km (useful for agglomerations, e.g. 30 for Trójmiasto) or null\n\n'
        f"For agglomerations: Trójmiasto → location='gdansk', location_radius=30; "
        f"Śląsk → location='katowice', location_radius=40.\n\n"
        f"--- OLX URL STRUCTURE CONTEXT ---\n{url_context}\n---------------------------------\n\n"
        f"Return ONLY valid JSON. No markdown fences, no explanation."
    )

    response_text = await ask_llm(prompt)
    json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not json_match:
        return None

    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return None


# ============================================================================
# CHEAP MODE LLM (NL query → product list)
# ============================================================================

async def run_cheap_mode_llm(user_query: str, categories: list = None) -> Optional[dict]:
    """
    Turn a request into a list of specific product models.
    Returns {"name": str, "products": [...], "max_price": int|None,
             "base_path": str|None, "condition": str|None,
             "browse_category": str|None,
             "location": str|None, "location_radius": int|None} or None.
    """
    from config import OLX_CATEGORIES
    cats = categories or OLX_CATEGORIES
    # Build a hint showing only deep leaf categories (3+ segments) for browse_category
    leaf_cats = [c for c in cats if c.count("/") >= 2]
    prompt = (
        f"You are a product recommendation assistant for OLX.pl (Polish marketplace).\n"
        f'User wants: "{user_query}"\n\n'
        f"Suggest 5–8 specific product model names that match what the user wants "
        f"and that realistically appear on OLX Poland. Include Polish and international brands.\n"
        f"**IMPORTANT: Use SHORT, search-friendly names — brand + model only.** "
        f"Do NOT include specs like engine size, screen size, key count, switch type etc. "
        f"Those are handled by category and price filters. "
        f"Examples: 'Honda PCX' not 'Honda PCX 125', 'Yamaha NMAX' not 'Yamaha NMAX 125', "
        f"'Logitech G102' not 'Logitech G102 Lightsync'.\n\n"
        f"Also extract a short search name (1-3 words, NO specs like '125cc') and max price in PLN if mentioned.\n"
        f"If the user specifies a price range (e.g. '200-500 PLN'), extract both min_price and max_price.\n\n"
        f"Pick the best OLX category path for these products from this list:\n"
        f"{json.dumps(cats, indent=2)}\n"
        f'Use "oferty" (all categories) only if no specific category fits.\n\n'
        f'If the user wants used/new items, set "condition" to "used" or "new". Otherwise null.\n\n'
        f"If the user specifies a location/city/region, extract it as a URL slug "
        f"(lowercase, no diacritics, e.g. 'gdansk', 'warszawa', 'krakow').\n"
        f"For regions/voivodeships, use the slug (e.g. 'pomorskie', 'mazowieckie', 'slaskie').\n"
        f"For agglomerations (e.g. Trójmiasto, Śląsk), use the main city slug and set location_radius "
        f"to cover the area in km (e.g. Trójmiasto → location='gdansk', location_radius=30).\n\n"
        f"**Browse category**: Pick the most specific leaf subcategory from this list "
        f"that matches the products. This is used to browse ALL new listings in that subcategory "
        f"(no keyword needed), catching generic-titled listings that keyword searches miss.\n"
        f"Leaf categories:\n{json.dumps(leaf_cats, indent=2)}\n"
        f"Set browse_category to the best match, or null if no specific subcategory fits.\n\n"
        f"**Custom filters**: If the user specifies numeric specs (engine size, year range, mileage, etc.), "
        f"extract them as OLX filter parameters instead of putting them in product names or the search name.\n"
        f"Available OLX numeric filters: enginesize (cc), year, milage (km), enginepower (HP).\n"
        f'Use "key:from" / "key:to" syntax for ranges. Examples:\n'
        f'  "up to 125cc" → {{"enginesize:to": 125}}\n'
        f'  "from 2010" → {{"year:from": 2010}}\n'
        f'  "50-125cc" → {{"enginesize:from": 50, "enginesize:to": 125}}\n'
        f"Set custom_filters to null if no numeric specs are mentioned.\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"name": "short label", "max_price": 200, "min_price": null, '
        f'"products": ["Model A", "Model B", ...], '
        f'"base_path": "elektronika/komputery", "condition": "used", '
        f'"browse_category": "elektronika/komputery/akcesoria-komputerowe/klawiatury", '
        f'"custom_filters": {{"enginesize:to": 125}}, '
        f'"location": "gdansk", "location_radius": 30}}\n'
        f"max_price, min_price, base_path, condition, browse_category, custom_filters, location, location_radius should be null if not specified. "
        f"No markdown, no explanation."
    )
    response_text = await ask_llm(prompt)
    json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not json_match:
        return None
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return None


async def run_cheap_feedback_llm(
    original_query: str, current_products: list, feedback_history: list,
) -> Optional[list]:
    """Refine the product list based on accumulated feedback."""
    feedback_str = "\n".join(
        f'- "{f["listing_title"]}" ({f["product"]}): {f["feedback"]}'
        for f in feedback_history
    )
    prompt = (
        f"You are refining a product list for an OLX.pl buyer.\n"
        f'Original request: "{original_query}"\n'
        f"Current product list: {json.dumps(current_products, ensure_ascii=False)}\n\n"
        f"User feedback on seen listings:\n{feedback_str}\n\n"
        f"Return an updated list of 5–8 specific product models that better match the user's "
        f"preferences based on the feedback. Replace the old list entirely.\n"
        f"**Use SHORT names — brand + model only.** No specs like engine size, screen size, etc. "
        f"Example: 'Honda PCX' not 'Honda PCX 125'.\n"
        f'Return ONLY a JSON array: ["Model A", "Model B", ...]\n'
        f"No markdown, no explanation."
    )
    response_text = await ask_llm(prompt)
    arr_match = re.search(r"\[.*\]", response_text, re.DOTALL)
    if not arr_match:
        return None
    try:
        result = json.loads(arr_match.group())
        return result if isinstance(result, list) else None
    except json.JSONDecodeError:
        return None


# ============================================================================
# /SLOPGEST — AI SUGGESTION REPORT
# ============================================================================

_SLOPGEST_PROMPT = """\
You are an expert marketplace analyst for OLX.pl (Polish marketplace).
You have access to a database with the user's searches, listings, feedback, and market data.

**Your task:** Analyze the user's data and produce a concise, actionable report in Markdown.

**Steps:**
1. Use `get_schema` to understand the database structure.
2. Query the `searches` table filtered by chat_id = '{chat_id}' to see what the user is looking for.
3. Query `search_listings` joined with `listings` to see what's been accepted, declined, and sent.
4. Query `feedback` to understand user preferences and dislikes.
5. Query `seen_listings` to gauge market volume.
6. Look at price distributions of accepted vs declined listings.

**Report structure:**
## 📊 Market Overview
- How many searches, listings seen, accepted, declined

## 💡 Suggestions
- Based on accepted listings and feedback, suggest specific product models or search terms that would yield better results
- If prices seem high, suggest alternative models or waiting strategies
- If the user is too picky (high decline rate), suggest broadening criteria

## 🏆 Best Deals Found
- Highlight the top 3-5 accepted listings by value (lowest price for the category)

## 🔮 Recommendations
- Specific actionable advice: add/remove searches, adjust price range, try different keywords

Keep the report concise (under 500 words). Use Polish product names where appropriate.
Reply with ONLY the markdown report, no preamble.
"""


async def run_slopgest_llm(chat_id: str) -> str:
    """Run the /slopgest analysis with MCP database tool access."""
    prompt = _SLOPGEST_PROMPT.format(chat_id=chat_id)
    response = await ask_llm(
        prompt,
        mcp=True,
        timeout=180,
    )
    return response.strip() if response else ""
