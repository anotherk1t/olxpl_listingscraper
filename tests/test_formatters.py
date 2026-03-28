"""Tests for the formatters module."""

from formatters import (
    build_cheap_confirmation,
    build_slopsearch_confirmation,
    cheap_price_stats,
    format_advisor_report,
    format_cheap_listing,
    parse_price,
)


# ---------------------------------------------------------------------------
# parse_price
# ---------------------------------------------------------------------------
class TestParsePrice:
    def test_simple_price(self):
        assert parse_price("150 zł") == 150.0

    def test_price_with_thousands_space(self):
        assert parse_price("1 200 zł") == 1200.0

    def test_price_with_comma_decimal(self):
        assert parse_price("15,50 zł") == 15.50

    def test_large_price(self):
        assert parse_price("12 500 zł") == 12500.0

    def test_za_darmo(self):
        assert parse_price("Za darmo") == 0.0

    def test_zamienie(self):
        assert parse_price("Zamienię") == 0.0

    def test_empty_string(self):
        assert parse_price("") == 0.0

    def test_none(self):
        assert parse_price(None) == 0.0

    def test_only_currency_symbol(self):
        assert parse_price("zł") == 0.0

    def test_do_negocjacji(self):
        assert parse_price("do negocjacji") == 0.0

    def test_price_with_dot_decimal(self):
        assert parse_price("99.99 zł") == 99.99

    def test_price_no_currency(self):
        assert parse_price("350") == 350.0


# ---------------------------------------------------------------------------
# cheap_price_stats
# ---------------------------------------------------------------------------
class TestCheapPriceStats:
    def test_returns_empty_for_single_listing(self):
        listings = [{"price": "200 zł"}]
        assert cheap_price_stats(listings) == ""

    def test_returns_empty_for_zero_prices(self):
        listings = [{"price": "Za darmo"}, {"price": "Zamienię"}]
        assert cheap_price_stats(listings) == ""

    def test_stats_for_two_listings(self):
        listings = [{"price": "100 zł"}, {"price": "200 zł"}]
        result = cheap_price_stats(listings)
        assert "avg 150 PLN" in result
        assert "median 150 PLN" in result
        assert "2 listings" in result

    def test_stats_for_multiple_listings(self):
        listings = [
            {"price": "50 zł"},
            {"price": "150 zł"},
            {"price": "250 zł"},
            {"price": "350 zł"},
            {"price": "1 000 zł"},
        ]
        result = cheap_price_stats(listings)
        assert "📊" in result
        assert "5 listings" in result
        assert "avg 360 PLN" in result
        assert "median 250 PLN" in result

    def test_ignores_zero_prices(self):
        listings = [
            {"price": "100 zł"},
            {"price": "Za darmo"},
            {"price": "300 zł"},
        ]
        result = cheap_price_stats(listings)
        assert "2 listings" in result

    def test_empty_list(self):
        assert cheap_price_stats([]) == ""


# ---------------------------------------------------------------------------
# build_slopsearch_confirmation
# ---------------------------------------------------------------------------
class TestBuildSlopsearchConfirmation:
    def test_contains_all_fields(self):
        refined = {
            "name": "iPhone 13",
            "min_price": 1500,
            "max_price": 3000,
            "condition": "used",
            "keywords": ["iphone", "13", "apple"],
            "url": "https://www.olx.pl/elektronika/telefony/q-iphone-13/",
        }
        result = build_slopsearch_confirmation(refined)
        assert "*Name:* iPhone 13" in result
        assert "1500–3000 PLN" in result
        assert "*Condition:* used" in result
        assert "iphone, 13, apple" in result
        assert "https://www.olx.pl/elektronika/telefony/q-iphone-13/" in result
        assert "Do you approve this search?" in result

    def test_max_price_only(self):
        refined = {
            "name": "Rower",
            "min_price": None,
            "max_price": 500,
            "condition": None,
            "keywords": ["rower"],
            "url": "https://www.olx.pl/sport-hobby/rowery/",
        }
        result = build_slopsearch_confirmation(refined)
        assert "up to 500 PLN" in result
        assert "*Condition:* any" in result

    def test_min_price_only(self):
        refined = {
            "name": "Laptop",
            "min_price": 2000,
            "max_price": None,
            "condition": "new",
            "keywords": ["laptop"],
            "url": "https://www.olx.pl/elektronika/komputery/",
        }
        result = build_slopsearch_confirmation(refined)
        assert "from 2000 PLN" in result

    def test_no_price(self):
        refined = {
            "name": "Szafka",
            "min_price": None,
            "max_price": None,
            "condition": None,
            "keywords": [],
            "url": "https://www.olx.pl/dom-ogrod/meble/",
        }
        result = build_slopsearch_confirmation(refined)
        assert "*Price:* any" in result

    def test_url_fallback_note(self):
        refined = {
            "name": "Stół",
            "min_price": None,
            "max_price": None,
            "condition": None,
            "keywords": ["stół"],
            "url": "https://www.olx.pl/",
            "url_fallback": True,
        }
        result = build_slopsearch_confirmation(refined)
        assert "fallback broad URL" in result


# ---------------------------------------------------------------------------
# build_cheap_confirmation
# ---------------------------------------------------------------------------
class TestBuildCheapConfirmation:
    def test_basic_output(self):
        data = {
            "name": "Zimowe opony",
            "products": ["Michelin Alpin 6", "Continental WinterContact"],
            "min_price": 100,
            "max_price": 400,
        }
        result = build_cheap_confirmation(data)
        assert "Zimowe opony" in result
        assert "100–400 PLN" in result
        assert "Michelin Alpin 6" in result
        assert "Continental WinterContact" in result
        assert "I'll search OLX" in result

    def test_under_max_price(self):
        data = {
            "name": "Karta graficzna",
            "products": ["RTX 3060"],
            "min_price": None,
            "max_price": 1200,
        }
        result = build_cheap_confirmation(data)
        assert "under 1200 PLN" in result

    def test_from_min_price(self):
        data = {
            "name": "Monitor",
            "products": ["Dell S2722QC"],
            "min_price": 800,
            "max_price": None,
        }
        result = build_cheap_confirmation(data)
        assert "from 800 PLN" in result

    def test_any_price(self):
        data = {
            "name": "Krzesło biurowe",
            "products": ["IKEA Markus"],
            "min_price": None,
            "max_price": None,
        }
        result = build_cheap_confirmation(data)
        assert "any price" in result

    def test_with_custom_filters(self):
        data = {
            "name": "Samochód",
            "products": ["Volkswagen Golf VII"],
            "min_price": 15000,
            "max_price": 35000,
            "custom_filters": {
                "year:from": "2015",
                "milage:to": "150000",
                "enginepower:from": "150",
            },
        }
        result = build_cheap_confirmation(data)
        assert "🔧 Filters:" in result
        assert "Year ≥ 2015" in result
        assert "Mileage ≤ 150000" in result
        assert "Power ≥ 150" in result

    def test_without_custom_filters(self):
        data = {
            "name": "Telewizor",
            "products": ["LG OLED C3"],
            "min_price": 2000,
            "max_price": 5000,
        }
        result = build_cheap_confirmation(data)
        assert "🔧" not in result

    def test_with_browse_category(self):
        data = {
            "name": "Rower",
            "products": ["Kross Level"],
            "min_price": None,
            "max_price": 2000,
            "browse_category": "sport-hobby/rowery",
        }
        result = build_cheap_confirmation(data)
        assert "🔍 Browse:" in result
        assert "sport-hobby/rowery" in result


# ---------------------------------------------------------------------------
# format_cheap_listing
# ---------------------------------------------------------------------------
class TestFormatCheapListing:
    def _make_args(self, **overrides):
        defaults = {
            "search_name": "Zimowe opony",
            "product": "Michelin Alpin 6 205/55 R16",
            "listing": {
                "title": "Opony zimowe Michelin Alpin 6 205/55R16 — komplet",
                "price": "480 zł",
                "url": "https://www.olx.pl/d/oferta/opony-zimowe-michelin-ID123.html",
            },
            "details": {"location": "Warszawa, Mokotów", "condition": "Używane"},
            "summary": "Komplet 4 opon, stan dobry, bieżnik 5mm.",
            "stats_line": "📊 Approved so far: avg 420 PLN | median 400 PLN (6 listings)",
        }
        defaults.update(overrides)
        return defaults

    def test_full_message(self):
        args = self._make_args()
        result = format_cheap_listing(**args)
        assert "💸 *Zimowe opony* — Michelin Alpin 6 205/55 R16" in result
        assert "480 zł" in result
        assert "📍 Warszawa, Mokotów" in result
        assert "🏷️ Używane" in result
        assert "Komplet 4 opon" in result
        assert "📊 Approved so far:" in result
        assert "View on OLX" in result
        assert "Reply to this message" in result

    def test_without_location_and_condition(self):
        args = self._make_args(details={})
        result = format_cheap_listing(**args)
        assert "📍" not in result
        assert "🏷️" not in result

    def test_without_summary(self):
        args = self._make_args(summary="")
        result = format_cheap_listing(**args)
        assert "🤖" not in result

    def test_without_stats(self):
        args = self._make_args(stats_line="")
        result = format_cheap_listing(**args)
        assert "📊" not in result


# ---------------------------------------------------------------------------
# format_advisor_report
# ---------------------------------------------------------------------------
class TestFormatAdvisorReport:
    def test_report_with_suggestions(self):
        advice = {
            "search": {"name": "Zimowe opony 205/55 R16"},
            "coverage_summary": "5 products tracked\n3 with recent listings",
            "suggestions": [
                {
                    "type": "add_product",
                    "label": "Add Goodyear UltraGrip 9+",
                    "reason": "Popular winter tyre missing from your list",
                },
                {
                    "type": "raise_price",
                    "label": "Raise max price to 500 PLN",
                    "reason": "Many good listings just above your limit",
                },
            ],
        }
        result = format_advisor_report(advice)
        assert "Advisor Report: Zimowe opony 205/55 R16" in result
        assert "Coverage:" in result
        assert "5 products tracked" in result
        assert "➕ Add Goodyear UltraGrip 9+" in result
        assert "Popular winter tyre" in result
        assert "💰 Raise max price to 500 PLN" in result

    def test_report_no_suggestions(self):
        advice = {
            "search": {"name": "Monitor Dell"},
            "coverage_summary": "2 products tracked",
            "suggestions": [],
        }
        result = format_advisor_report(advice)
        assert "Advisor Report: Monitor Dell" in result
        assert "search looks healthy" in result

    def test_all_suggestion_types(self):
        advice = {
            "search": {"name": "Test"},
            "coverage_summary": "ok",
            "suggestions": [
                {"type": "add_product", "label": "Add X", "reason": "r1"},
                {"type": "remove_product", "label": "Remove Y", "reason": "r2"},
                {"type": "raise_price", "label": "Raise to 100", "reason": "r3"},
                {"type": "expand_location", "label": "Add Kraków", "reason": "r4"},
                {"type": "unknown_type", "label": "Something", "reason": "r5"},
            ],
        }
        result = format_advisor_report(advice)
        assert "➕ Add X" in result
        assert "➖ Remove Y" in result
        assert "💰 Raise to 100" in result
        assert "📍 Add Kraków" in result
        assert "💡 Something" in result
