"""
OLX Scraper — Message Formatters

Helpers for building Telegram message text.
"""

import re
import i18n


def parse_price(price_str: str) -> float:
    """Extract numeric price from string (e.g., '1 500 zł' -> 1500.0)."""
    if not price_str:
        return 0.0
    try:
        cleaned = re.sub(r"[^\d,.]", "", price_str).replace(",", ".")
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def cheap_price_stats(accepted_listings: list) -> str:
    """Return a formatted price stats line from approved cheap listings."""
    prices = [parse_price(l.get("price", "")) for l in accepted_listings]
    prices = [p for p in prices if p > 0]
    if len(prices) < 2:
        return ""
    prices.sort()
    avg = sum(prices) / len(prices)
    mid = len(prices) // 2
    median = (prices[mid] + prices[~mid]) / 2.0
    return f"📊 Approved so far: avg {avg:.0f} PLN | median {median:.0f} PLN ({len(prices)} listings)"


def build_slopsearch_confirmation(refined: dict, lang: str = "en") -> str:
    """Format the slopsearch confirmation preview."""
    url_note = " ⚠️ *(fallback broad URL)*" if refined.get("url_fallback") else ""
    min_p = refined.get("min_price")
    max_p = refined.get("max_price")
    if min_p and max_p:
        price_str = f"{int(min_p)}–{int(max_p)} PLN"
    elif max_p:
        price_str = f"up to {int(max_p)} PLN"
    elif min_p:
        price_str = f"from {int(min_p)} PLN"
    else:
        price_str = "any"
    return (
        f"Here is what I understood:\n"
        f"*Name:* {refined.get('name')}\n"
        f"*Price:* {price_str}\n"
        f"*Condition:* {refined.get('condition') or 'any'}\n"
        f"*Keywords:* {', '.join(refined.get('keywords', []))}\n"
        f"*Search URL:* `{refined.get('url')}`{url_note}\n\n"
        f"Do you approve this search?"
    )


def build_cheap_confirmation(data: dict, lang: str = "en") -> str:
    """Format the cheap mode confirmation preview."""
    products = data.get("products", [])
    name = data.get("name", "?")
    min_price = data.get("min_price")
    max_price = data.get("max_price")
    if min_price and max_price:
        price_str = f"{int(min_price)}–{int(max_price)} PLN"
    elif max_price:
        price_str = f"under {int(max_price)} PLN"
    elif min_price:
        price_str = f"from {int(min_price)} PLN"
    else:
        price_str = "any price"
    product_list = "\n".join(f"  • {p}" for p in products)
    browse_cat = data.get("browse_category")
    browse_line = f"\n🔍 Browse: `{browse_cat}`" if browse_cat else ""
    custom_filters = data.get("custom_filters")
    filters_line = ""
    if custom_filters:
        filter_labels = {
            "enginesize": "Engine", "year": "Year", "milage": "Mileage", "enginepower": "Power",
        }
        parts = []
        for key, val in custom_filters.items():
            base, direction = key.split(":") if ":" in key else (key, "")
            label = filter_labels.get(base, base)
            if direction == "to":
                parts.append(f"{label} ≤ {val}")
            elif direction == "from":
                parts.append(f"{label} ≥ {val}")
            else:
                parts.append(f"{label}: {val}")
        filters_line = f"\n🔧 Filters: {', '.join(parts)}"
    return (
        f"💸 *Cheap Mode Search: {name}*\n\n"
        f"💰 Price: {price_str}\n\n"
        f"🛒 Products I'll watch:\n{product_list}{browse_line}{filters_line}\n\n"
        f"I'll search OLX for each model and send listings immediately."
    )


def format_monitor_listing(search_name: str, listing: dict) -> str:
    """Format a new monitor-mode listing notification."""
    return (
        f"✨ *New: {search_name}*\n\n"
        f"*{listing['title']}*\n\n"
        f"💰 {listing['price']}\n"
        f"🔗 [View]({listing['url']})"
    )


def format_cheap_listing(
    search_name: str, product: str, listing: dict,
    details: dict, summary: str, stats_line: str,
) -> str:
    """Format a cheap-mode listing message."""
    parts = [
        f"💸 *{search_name}* — {product}\n",
        f"*{listing['title']}*",
        f"💰 {listing['price']}",
    ]
    location = details.get("location", "")
    condition = details.get("condition", "")
    if location:
        parts.append(f"📍 {location}")
    if condition:
        parts.append(f"🏷️ {condition}")
    if summary:
        parts.append(f"\n🤖 _{summary}_")
    if stats_line:
        parts.append(f"\n{stats_line}")
    parts.append(f"\n🔗 [View on OLX]({listing['url']})")
    parts.append("_Reply to this message with feedback to refine the search._")
    return "\n".join(parts)


def format_cheap_product_group(
    search_name: str, product: str,
    listings: list[dict], details_list: list[dict], verdicts: list[dict],
    stats_line: str,
) -> str:
    """Format a grouped message for all passed listings of one product."""
    # Price stats for this batch
    prices = [parse_price(l.get("price", "")) for l in listings]
    prices_pos = [p for p in prices if p > 0]
    if prices_pos:
        avg = sum(prices_pos) / len(prices_pos)
        prices_sorted = sorted(prices_pos)
        mid = len(prices_sorted) // 2
        median = (prices_sorted[mid] + prices_sorted[~mid]) / 2.0
        batch_stats = f"📊 {len(listings)} listings | avg {avg:.0f} PLN | median {median:.0f} PLN | range {min(prices_pos):.0f}–{max(prices_pos):.0f} PLN"
    else:
        batch_stats = f"📊 {len(listings)} listings"

    parts = [
        f"💸 *{search_name}* — {product}",
        batch_stats,
    ]
    if stats_line:
        parts.append(stats_line)
    parts.append("")  # blank line

    for i, (listing, det, verdict) in enumerate(zip(listings, details_list, verdicts), 1):
        location = det.get("location", "")
        condition = det.get("condition", "")
        summary = verdict.get("summary", "")
        loc_str = f" 📍{location}" if location else ""
        cond_str = f"🏷️ {condition}" if condition else ""
        summary_str = f" | _{summary}_" if summary else ""
        line = f"{i}. [{listing['title']}]({listing['url']}) — {listing['price']}{loc_str}"
        if cond_str or summary_str:
            line += f"\n   {cond_str}{summary_str}"
        parts.append(line)

    parts.append("\n_Reply to this message with feedback to refine the search._")
    return "\n".join(parts)


def format_review_item(search_name: str, pending_count: int, listing: dict) -> str:
    """Format a slopsearch review item."""
    return (
        f"🔎 *Reviewing: {search_name}*\n"
        f"({pending_count} items remaining)\n\n"
        f"*{listing['title']}*\n"
        f"💰 {listing['price']}\n"
        f"🔗 [View Listing]({listing['url']})"
    )


def format_market_summary(search_name: str, accepted_count: int, prices: list[float]) -> str:
    """Format market summary after finishing review."""
    if not prices:
        return (
            f"🏁 Review finished for '{search_name}'. "
            f"Accepted {accepted_count} items.\nNow monitoring for new matches."
        )
    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    prices_sorted = sorted(prices)
    mid = len(prices_sorted) // 2
    median_price = (prices_sorted[mid] + prices_sorted[~mid]) / 2.0
    return (
        f"📊 *Market Summary: {search_name}*\n\n"
        f"Based on {accepted_count} approved listings:\n"
        f"📉 Lowest Price: {min_price:.0f} PLN\n"
        f"📈 Average Price: {avg_price:.0f} PLN\n"
        f"🎯 Median Price: {median_price:.0f} PLN\n\n"
        f"Bot is now actively monitoring for new matches!"
    )


def format_advisor_report(advice: dict) -> str:
    """Format the advisor report for Telegram."""
    search = advice["search"]
    name = search.get("name", "?")
    summary = advice.get("coverage_summary", "No data")
    suggestions = advice.get("suggestions", [])

    lines = [f"🔍 *Advisor Report: {name}*\n"]
    lines.append("*Coverage:*")
    lines.append(f"```\n{summary}\n```")

    if suggestions:
        lines.append("\n*Suggestions:*")
        for i, s in enumerate(suggestions):
            emoji = {"add_product": "➕", "remove_product": "➖",
                     "raise_price": "💰", "expand_location": "📍"}.get(s.get("type"), "💡")
            lines.append(f"{emoji} {s.get('label', '?')}")
            lines.append(f"   _{s.get('reason', '')}_")
    else:
        lines.append("\n✅ No changes suggested — search looks healthy!")

    return "\n".join(lines)
