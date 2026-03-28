"""Tests for the scraper module — JSON-LD parsing, HTML card parsing, and full pipeline."""

import json
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from scraper import _parse_html_cards, _parse_json_ld, scrape_olx_page

# ============================================================================
# Fixture HTML fragments
# ============================================================================

JSONLD_PAYLOAD = json.dumps(
    {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1},
            {"@type": "ListItem", "position": 2},
        ],
        "offers": {
            "@type": "AggregateOffer",
            "offers": [
                {
                    "@type": "Offer",
                    "name": "iPhone 14 Pro  128 GB",
                    "price": 3200,
                    "priceCurrency": "PLN",
                    "url": "https://www.olx.pl/d/oferta/iphone-14-pro-128gb-IDABCd3.html",
                },
                {
                    "@type": "Offer",
                    "name": "Samsung Galaxy S23",
                    "price": 2100,
                    "priceCurrency": "PLN",
                    "url": "https://www.olx.pl/d/oferta/samsung-galaxy-s23-IDxyz99.html",
                },
            ],
        },
    }
)

HTML_WITH_JSONLD = (
    f'<html><head><script type="application/ld+json">{JSONLD_PAYLOAD}</script></head><body></body></html>'
)

HTML_CARD_TEMPLATE = """
<html><body>
<div data-cy="l-card" id="123456">
  <a href="/d/oferta/rower-gorski-ID123456.html">
    <h4>Rower górski Kross</h4>
  </a>
  <p data-testid="ad-price">1 250 zł</p>
  <p data-testid="location-date">Warszawa, Mokotów - 12 maja 2025</p>
</div>
<div data-cy="l-card" id="789012">
  <a href="https://www.olx.pl/d/oferta/laptop-lenovo-ID789012.html">
    <h4>Laptop Lenovo ThinkPad</h4>
  </a>
  <p data-testid="ad-price">2 800 zł</p>
  <p data-testid="location-date">Kraków - 10 maja 2025</p>
</div>
</body></html>
"""

FULL_PAGE_HTML = (
    "<html><head>"
    f'<script type="application/ld+json">{JSONLD_PAYLOAD}</script>'
    "</head><body>"
    '<div data-cy="l-card" id="ABCd3">'
    '  <a href="/d/oferta/iphone-14-pro-128gb-IDABCd3.html"><h4>iPhone 14 Pro</h4></a>'
    '  <p data-testid="ad-price">3 200 zł</p>'
    '  <p data-testid="location-date">Gdańsk, Śródmieście - 5 maja 2025</p>'
    "</div>"
    '<div data-cy="l-card" id="xyz99">'
    '  <a href="/d/oferta/samsung-galaxy-s23-IDxyz99.html"><h4>Samsung Galaxy S23</h4></a>'
    '  <p data-testid="ad-price">2 100 zł</p>'
    '  <p data-testid="location-date">Poznań - 4 maja 2025</p>'
    "</div>"
    "</body></html>"
)


# ============================================================================
# _parse_json_ld tests
# ============================================================================


class TestParseJsonLd:
    def test_extracts_all_listings(self):
        soup = BeautifulSoup(HTML_WITH_JSONLD, "lxml")
        listings = _parse_json_ld(soup)
        assert len(listings) == 2

    def test_extracted_fields(self):
        soup = BeautifulSoup(HTML_WITH_JSONLD, "lxml")
        listings = _parse_json_ld(soup)
        first = listings[0]

        assert first["title"] == "iPhone 14 Pro 128 GB"
        assert first["price"] == "3200 PLN"
        assert first["url"] == "https://www.olx.pl/d/oferta/iphone-14-pro-128gb-IDABCd3.html"
        assert first["id"] == "ABCd3"

    def test_second_listing(self):
        soup = BeautifulSoup(HTML_WITH_JSONLD, "lxml")
        listings = _parse_json_ld(soup)
        second = listings[1]

        assert second["title"] == "Samsung Galaxy S23"
        assert second["price"] == "2100 PLN"
        assert second["id"] == "xyz99"

    def test_id_extraction_from_url_pattern(self):
        """ID is extracted from the -ID<id>.html pattern in the URL."""
        soup = BeautifulSoup(HTML_WITH_JSONLD, "lxml")
        listings = _parse_json_ld(soup)
        assert listings[0]["id"] == "ABCd3"
        assert listings[1]["id"] == "xyz99"

    def test_whitespace_normalised_in_title(self):
        """Multiple spaces in the offer name are collapsed to one."""
        soup = BeautifulSoup(HTML_WITH_JSONLD, "lxml")
        listings = _parse_json_ld(soup)
        # The fixture name has double space: "iPhone 14 Pro  128 GB"
        assert "  " not in listings[0]["title"]
        assert listings[0]["title"] == "iPhone 14 Pro 128 GB"


# ============================================================================
# _parse_html_cards tests
# ============================================================================


class TestParseHtmlCards:
    def test_extracts_all_cards(self):
        soup = BeautifulSoup(HTML_CARD_TEMPLATE, "lxml")
        listings = _parse_html_cards(soup)
        assert len(listings) == 2

    def test_relative_url_gets_base(self):
        soup = BeautifulSoup(HTML_CARD_TEMPLATE, "lxml")
        listings = _parse_html_cards(soup)
        first = listings[0]
        assert first["url"].startswith("https://www.olx.pl")
        assert first["url"].endswith("ID123456.html")

    def test_absolute_url_kept(self):
        soup = BeautifulSoup(HTML_CARD_TEMPLATE, "lxml")
        listings = _parse_html_cards(soup)
        second = listings[1]
        assert second["url"] == "https://www.olx.pl/d/oferta/laptop-lenovo-ID789012.html"

    def test_fields(self):
        soup = BeautifulSoup(HTML_CARD_TEMPLATE, "lxml")
        listings = _parse_html_cards(soup)
        first = listings[0]
        assert first["id"] == "123456"
        assert first["title"] == "Rower górski Kross"
        assert first["price"] == "1 250 zł"

    def test_location_extracted(self):
        soup = BeautifulSoup(HTML_CARD_TEMPLATE, "lxml")
        listings = _parse_html_cards(soup)
        assert listings[0]["location"] == "Warszawa, Mokotów"
        assert listings[1]["location"] == "Kraków"


# ============================================================================
# scrape_olx_page tests (mocked HTTP)
# ============================================================================


class TestScrapeOlxPage:
    @patch("scraper.requests.get")
    def test_json_ld_pipeline(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = FULL_PAGE_HTML
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        listings = scrape_olx_page("https://www.olx.pl/elektronika/telefony/q-iphone/")
        assert len(listings) == 2
        assert listings[0]["title"] == "iPhone 14 Pro 128 GB"
        assert listings[0]["url"].startswith("https://www.olx.pl")

    @patch("scraper.requests.get")
    def test_location_enriched_from_html(self, mock_get):
        """JSON-LD listings get location from matching HTML cards."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = FULL_PAGE_HTML
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        listings = scrape_olx_page("https://www.olx.pl/elektronika/")
        locations = [l.get("location", "") for l in listings]
        assert "Gdańsk, Śródmieście" in locations
        assert "Poznań" in locations

    @patch("scraper.requests.get")
    def test_falls_back_to_html_when_no_jsonld(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_CARD_TEMPLATE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        listings = scrape_olx_page("https://www.olx.pl/elektronika/")
        # HTML cards have relative URLs that get olx.pl base prepended
        assert len(listings) == 2

    @patch("scraper.requests.get")
    def test_non_olx_urls_filtered(self, mock_get):
        """Listings with non-olx.pl URLs (e.g. otomoto.pl) are discarded."""
        payload = json.dumps(
            {
                "offers": {
                    "offers": [
                        {
                            "name": "Car on OtoMoto",
                            "price": 50000,
                            "priceCurrency": "PLN",
                            "url": "https://www.otomoto.pl/oferta/car-ID999.html",
                        },
                        {
                            "name": "Phone on OLX",
                            "price": 1500,
                            "priceCurrency": "PLN",
                            "url": "https://www.olx.pl/d/oferta/phone-IDabc.html",
                        },
                    ]
                }
            }
        )
        html = f'<html><head><script type="application/ld+json">{payload}</script></head><body></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        listings = scrape_olx_page("https://www.olx.pl/motoryzacja/")
        assert len(listings) == 1
        assert "olx.pl" in listings[0]["url"]

    @patch("scraper.requests.get")
    def test_request_failure_returns_empty(self, mock_get):
        import requests as req

        mock_get.side_effect = req.ConnectionError("timeout")

        listings = scrape_olx_page("https://www.olx.pl/bad/")
        assert listings == []


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    def test_empty_jsonld_script(self):
        html = '<html><head><script type="application/ld+json">{}</script></head><body></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _parse_json_ld(soup) == []

    def test_no_jsonld_script(self):
        html = "<html><head></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert _parse_json_ld(soup) == []

    def test_malformed_jsonld(self):
        html = '<html><head><script type="application/ld+json">NOT JSON</script></head><body></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _parse_json_ld(soup) == []

    def test_offer_without_url_skipped(self):
        payload = json.dumps(
            {
                "offers": {
                    "offers": [
                        {"name": "No URL item", "price": 100, "priceCurrency": "PLN"},
                        {
                            "name": "Has URL",
                            "price": 200,
                            "priceCurrency": "PLN",
                            "url": "https://www.olx.pl/d/oferta/item-IDabc.html",
                        },
                    ]
                }
            }
        )
        html = f'<html><head><script type="application/ld+json">{payload}</script></head><body></body></html>'
        soup = BeautifulSoup(html, "lxml")
        listings = _parse_json_ld(soup)
        assert len(listings) == 1
        assert listings[0]["title"] == "Has URL"

    def test_card_without_id_skipped(self):
        html = """
        <html><body>
        <div data-cy="l-card">
          <a href="/d/oferta/no-id.html"><h4>No ID</h4></a>
        </div>
        <div data-cy="l-card" id="good1">
          <a href="/d/oferta/good-IDgood1.html"><h4>Good</h4></a>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        listings = _parse_html_cards(soup)
        assert len(listings) == 1
        assert listings[0]["id"] == "good1"

    def test_card_without_link_skipped(self):
        html = """
        <html><body>
        <div data-cy="l-card" id="nolink">
          <h4>No Link</h4>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _parse_html_cards(soup) == []

    def test_missing_title_and_price(self):
        html = """
        <html><body>
        <div data-cy="l-card" id="bare1">
          <a href="/d/oferta/bare-IDbare1.html"></a>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        listings = _parse_html_cards(soup)
        assert len(listings) == 1
        assert listings[0]["title"] == "No Title"
        assert listings[0]["price"] == "No Price"

    def test_no_cards_in_html(self):
        html = "<html><body><p>Nothing here</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert _parse_html_cards(soup) == []

    def test_missing_location_date(self):
        html = """
        <html><body>
        <div data-cy="l-card" id="noloc">
          <a href="/d/oferta/item-IDnoloc.html"><h4>Item</h4></a>
          <p data-testid="ad-price">500 zł</p>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        listings = _parse_html_cards(soup)
        assert listings[0]["location"] == ""
