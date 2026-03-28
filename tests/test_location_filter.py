"""Tests for the location_filter module."""

import pytest

from location_filter import (
    _AGGLOMERATIONS,
    _CITY_SLUG_MAP,
    _VOIVODESHIPS,
    _normalize,
    filter_by_location,
    get_allowed_cities,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _listing(title: str, city_name: str, price: str = "1000 zł", url: str = "https://olx.pl/x") -> dict:
    """Build a mock listing.  ``city_name`` becomes the ``location`` field
    (the field that filter_by_location actually reads)."""
    return {"title": title, "price": price, "url": url, "city_name": city_name, "location": city_name}


# ── _normalize ───────────────────────────────────────────────────────────────


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("GDAŃSK") == "gdańsk"

    def test_strip_whitespace(self):
        assert _normalize("  Kraków  ") == "kraków"

    def test_preserves_polish_diacritics(self):
        """_normalize intentionally keeps diacritics — the city sets use them."""
        assert _normalize("Łódź") == "łódź"
        assert _normalize("WROCŁAW") == "wrocław"
        assert _normalize("Częstochowa") == "częstochowa"

    def test_empty_string(self):
        assert _normalize("") == ""


# ── voivodeship detection ────────────────────────────────────────────────────


class TestVoivodeshipDetection:
    ALL_VOIVODESHIPS = [
        "dolnoslaskie",
        "kujawsko-pomorskie",
        "lubelskie",
        "lubuskie",
        "lodzkie",
        "malopolskie",
        "mazowieckie",
        "opolskie",
        "podkarpackie",
        "podlaskie",
        "pomorskie",
        "slaskie",
        "swietokrzyskie",
        "warminsko-mazurskie",
        "wielkopolskie",
        "zachodniopomorskie",
    ]

    @pytest.mark.parametrize("voivodeship", ALL_VOIVODESHIPS)
    def test_voivodeship_returns_none(self, voivodeship: str):
        """Post-filter is skipped for voivodeships — OLX handles region filtering."""
        assert get_allowed_cities(voivodeship, None) is None

    def test_all_16_voivodeships_present(self):
        assert len(_VOIVODESHIPS) == 16
        assert set(self.ALL_VOIVODESHIPS) == _VOIVODESHIPS


# ── get_allowed_cities ───────────────────────────────────────────────────────


class TestGetAllowedCities:
    def test_none_location_returns_none(self):
        assert get_allowed_cities(None, None) is None

    def test_empty_string_returns_none(self):
        assert get_allowed_cities("", None) is None

    def test_known_city_slug_without_radius(self):
        result = get_allowed_cities("gdansk", None)
        assert result is not None
        assert isinstance(result, set)
        assert result == {"gdańsk"}

    def test_known_city_slug_with_small_radius(self):
        """Radius < 15 — still returns single city."""
        result = get_allowed_cities("gdansk", 10)
        assert result == {"gdańsk"}

    def test_known_city_slug_with_radius_expands(self):
        """Radius >= 15 on an agglomeration slug returns the full set."""
        result = get_allowed_cities("gdansk", 25)
        assert result is not None
        assert "gdańsk" in result
        assert "sopot" in result
        assert "gdynia" in result
        assert len(result) > 3

    def test_radius_threshold_15(self):
        """Exactly 15 km should trigger agglomeration expansion."""
        result = get_allowed_cities("warszawa", 15)
        assert result == _AGGLOMERATIONS["warszawa"]

    def test_radius_below_threshold(self):
        """14 km should NOT trigger agglomeration expansion."""
        result = get_allowed_cities("warszawa", 14)
        assert result == {"warszawa"}

    def test_unknown_slug_returned_as_is(self):
        result = get_allowed_cities("some-unknown-place", None)
        assert result == {"some-unknown-place"}

    def test_slug_case_insensitive(self):
        assert get_allowed_cities("GDANSK", None) == {"gdańsk"}
        assert get_allowed_cities("  Gdansk  ", None) == {"gdańsk"}

    @pytest.mark.parametrize("slug", list(_CITY_SLUG_MAP.keys()))
    def test_all_city_slugs_resolve(self, slug: str):
        result = get_allowed_cities(slug, None)
        assert result is not None
        assert _CITY_SLUG_MAP[slug] in result

    @pytest.mark.parametrize("slug", list(_AGGLOMERATIONS.keys()))
    def test_all_agglomerations_expand_with_radius(self, slug: str):
        result = get_allowed_cities(slug, 25)
        assert result == _AGGLOMERATIONS[slug]
        assert len(result) > 1


# ── filter_by_location ───────────────────────────────────────────────────────


class TestFilterByLocation:
    SAMPLE_LISTINGS = [
        _listing("Laptop Dell", "Gdańsk, Śródmieście"),
        _listing("iPhone 15", "Sopot"),
        _listing("PS5 Slim", "Kraków, Nowa Huta"),
        _listing("Monitor LG", "Gdynia, Orłowo"),
    ]

    def test_filters_to_single_city(self):
        result = filter_by_location(self.SAMPLE_LISTINGS, "gdansk", None)
        assert len(result) == 1
        assert result[0]["title"] == "Laptop Dell"

    def test_filters_with_agglomeration_radius(self):
        result = filter_by_location(self.SAMPLE_LISTINGS, "gdansk", 25)
        titles = {r["title"] for r in result}
        assert titles == {"Laptop Dell", "iPhone 15", "Monitor LG"}

    def test_no_location_slug_returns_all(self):
        result = filter_by_location(self.SAMPLE_LISTINGS, None, None)
        assert result == self.SAMPLE_LISTINGS

    def test_voivodeship_returns_all(self):
        result = filter_by_location(self.SAMPLE_LISTINGS, "pomorskie", None)
        assert result == self.SAMPLE_LISTINGS

    def test_empty_listings(self):
        result = filter_by_location([], "gdansk", None)
        assert result == []

    def test_listing_without_location_field_kept(self):
        """Listings with no location info are kept (benefit of the doubt)."""
        listings = [
            {"title": "Mystery item", "price": "500 zł", "url": "https://olx.pl/y"},
        ]
        result = filter_by_location(listings, "gdansk", 25)
        assert len(result) == 1

    def test_listing_with_empty_location_kept(self):
        listings = [_listing("Empty loc", "")]
        # Empty location string → listing.get("location") is "" → kept
        result = filter_by_location(listings, "gdansk", None)
        assert len(result) == 1

    def test_unknown_city_slug_filters(self):
        listings = [
            _listing("Item A", "some-unknown-place"),
            _listing("Item B", "Gdańsk"),
        ]
        result = filter_by_location(listings, "some-unknown-place", None)
        assert len(result) == 1
        assert result[0]["title"] == "Item A"

    def test_location_with_district_parsed(self):
        """'City, District' format — only the part before the comma matters."""
        listings = [_listing("Flat", "Gdańsk, Wrzeszcz")]
        result = filter_by_location(listings, "gdansk", None)
        assert len(result) == 1

    def test_all_rejected_when_no_match(self):
        listings = [
            _listing("A", "Kraków"),
            _listing("B", "Wrocław"),
        ]
        result = filter_by_location(listings, "gdansk", None)
        assert result == []
