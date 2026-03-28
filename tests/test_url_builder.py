"""Tests for url_builder.assemble_url, product_to_url, category_browse_url."""

import urllib.parse

from url_builder import assemble_url, category_browse_url, product_to_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_url(url: str) -> tuple[str, dict]:
    """Return (path, query_dict) for easy assertions."""
    parsed = urllib.parse.urlparse(url)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    return parsed.path, params


# ===== assemble_url =====


class TestAssembleUrl:
    def test_basic_assembly(self):
        url = assemble_url(
            base_path="elektronika",
            keyword="iPhone 15",
            location="warszawa",
            max_price=3000,
            min_price=1000,
            condition="used",
        )
        path, params = _parse_url(url)
        assert path == "/elektronika/warszawa/q-iphone-15/"
        assert params["search[filter_float_price:from]"] == "1000"
        assert params["search[filter_float_price:to]"] == "3000"
        assert params["search[filter_enum_state][0]"] == "used"

    def test_no_location(self):
        url = assemble_url(base_path="oferty", keyword="laptop")
        path, _ = _parse_url(url)
        assert path == "/oferty/q-laptop/"
        assert "?" not in url

    def test_no_keyword_whitespace(self):
        url = assemble_url(base_path="oferty", keyword="  ")
        path, _ = _parse_url(url)
        assert path == "/oferty/q-/"

    def test_condition_new(self):
        url = assemble_url(base_path="oferty", keyword="tv", condition="new")
        _, params = _parse_url(url)
        assert params["search[filter_enum_state][0]"] == "new"

    def test_condition_invalid_ignored(self):
        url = assemble_url(base_path="oferty", keyword="tv", condition="refurbished")
        assert "filter_enum_state" not in url

    def test_location_radius(self):
        url = assemble_url(
            base_path="oferty",
            keyword="bike",
            location="krakow",
            location_radius=25,
        )
        _, params = _parse_url(url)
        assert params["search[dist]"] == "25"

    def test_min_price_zero(self):
        url = assemble_url(base_path="oferty", keyword="x", min_price=0)
        _, params = _parse_url(url)
        assert params["search[filter_float_price:from]"] == "0"


# ===== product_to_url =====


class TestProductToUrl:
    def test_basic(self):
        url = product_to_url(
            product_name="Samsung Galaxy S24",
            max_price=2500,
            location="gdansk",
            condition="new",
        )
        path, params = _parse_url(url)
        assert "oferty" in path
        assert "gdansk" in path
        assert "samsung-galaxy-s24" in path
        assert params["search[filter_float_price:to]"] == "2500"
        assert params["search[filter_enum_state][0]"] == "new"

    def test_custom_filters(self):
        url = product_to_url(
            product_name="Honda CBR",
            base_path="motoryzacja/motocykle",
            custom_filters={"enginesize:to": 125, "year:from": 2010},
        )
        _, params = _parse_url(url)
        assert params["search[filter_float_enginesize:to]"] == "125"
        assert params["search[filter_float_year:from]"] == "2010"

    def test_custom_filters_enum(self):
        url = product_to_url(
            product_name="Rower",
            custom_filters={"colour": "red"},
        )
        _, params = _parse_url(url)
        assert params["search[filter_enum_colour][0]"] == "red"

    def test_no_location(self):
        url = product_to_url(product_name="Desk")
        path, _ = _parse_url(url)
        assert path == "/oferty/q-desk/"

    def test_none_custom_filters(self):
        url = product_to_url(product_name="Chair", custom_filters=None)
        assert "filter_float" not in url or "price" in url or "?" not in url

    def test_special_chars_stripped(self):
        url = product_to_url(product_name='TV 55" (LG)')
        assert "q-tv-55-lg" in url.lower()


# ===== category_browse_url =====


class TestCategoryBrowseUrl:
    def test_without_custom_filters(self):
        url = category_browse_url(
            category_path="elektronika/komputery",
            max_price=5000,
            min_price=500,
            condition="used",
            location="poznan",
        )
        path, params = _parse_url(url)
        assert path == "/elektronika/komputery/poznan/"
        assert params["search[order]"] == "created_at:desc"
        assert params["search[filter_float_price:from]"] == "500"
        assert params["search[filter_float_price:to]"] == "5000"
        assert params["search[filter_enum_state][0]"] == "used"

    def test_with_custom_filters(self):
        url = category_browse_url(
            category_path="motoryzacja/motocykle",
            custom_filters={"enginesize:to": 125},
        )
        _, params = _parse_url(url)
        assert params["search[filter_float_enginesize:to]"] == "125"
        assert params["search[order]"] == "created_at:desc"

    def test_no_location(self):
        url = category_browse_url(category_path="elektronika")
        path, params = _parse_url(url)
        assert path == "/elektronika/"
        assert params["search[order]"] == "created_at:desc"

    def test_none_custom_filters(self):
        url = category_browse_url(
            category_path="dom-ogrod",
            custom_filters=None,
        )
        assert "filter_float_enginesize" not in url

    def test_location_radius(self):
        url = category_browse_url(
            category_path="elektronika",
            location="wroclaw",
            location_radius=50,
        )
        _, params = _parse_url(url)
        assert params["search[dist]"] == "50"
