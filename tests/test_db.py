"""Comprehensive tests for the db module."""

import json
import sqlite3

import pytest

import db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the db module at a fresh SQLite file for every test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "_DB_PATH", db_file)

    def _test_connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(db, "_connect", _test_connect)
    db.init_db()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


class TestInitDb:
    def test_creates_tables(self):
        """init_db should create all expected tables."""
        with db.get_db() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        expected = {
            "searches",
            "search_urls",
            "seen_listings",
            "listings",
            "search_listings",
            "sent_messages",
            "feedback",
            "user_prefs",
        }
        assert expected.issubset(tables)

    def test_idempotent(self):
        """Calling init_db twice must not raise."""
        db.init_db()
        db.init_db()


# ---------------------------------------------------------------------------
# searches CRUD
# ---------------------------------------------------------------------------


class TestSearches:
    def test_create_and_get(self):
        sid = db.create_search("chat1", "my search", "monitor", url="https://olx.pl/x")
        assert isinstance(sid, int)

        s = db.get_search(sid)
        assert s is not None
        assert s["chat_id"] == "chat1"
        assert s["name"] == "my search"
        assert s["mode"] == "monitor"
        assert s["url"] == "https://olx.pl/x"
        assert s["status"] == "active"
        assert s["created_at"] > 0

    def test_create_with_all_fields(self):
        sid = db.create_search(
            "chat2",
            "full",
            "slopsearch",
            url="https://olx.pl",
            max_price=500.0,
            min_price=100.0,
            keywords=["laptop", "dell"],
            original_query="cheap dell laptop",
            products=["Dell Latitude", "Dell XPS"],
            location="gdansk",
            location_radius=50,
            base_path="elektronika/komputery",
            condition="used",
            browse_category="laptopy",
            custom_filters={"enginesize:to": 125},
            status="pending_scrape",
        )
        s = db.get_search(sid)
        assert s["max_price"] == 500.0
        assert s["min_price"] == 100.0
        assert s["keywords"] == ["laptop", "dell"]
        assert s["products"] == ["Dell Latitude", "Dell XPS"]
        assert s["location"] == "gdansk"
        assert s["location_radius"] == 50
        assert s["base_path"] == "elektronika/komputery"
        assert s["condition"] == "used"
        assert s["browse_category"] == "laptopy"
        # custom_filters is stored as JSON string (not deserialized by _row_to_dict)
        assert json.loads(s["custom_filters"]) == {"enginesize:to": 125}
        assert s["status"] == "pending_scrape"

    def test_get_nonexistent(self):
        assert db.get_search(9999) is None

    def test_unique_chat_name(self):
        db.create_search("c", "n", "monitor")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_search("c", "n", "monitor")

    # --- get_searches_by_chat ---

    def test_get_searches_by_chat_all(self):
        db.create_search("chat", "a", "monitor")
        db.create_search("chat", "b", "slopsearch")
        db.create_search("other", "c", "cheap")

        results = db.get_searches_by_chat("chat")
        assert len(results) == 2
        assert {r["name"] for r in results} == {"a", "b"}

    def test_get_searches_by_chat_filtered(self):
        db.create_search("chat", "a", "monitor")
        db.create_search("chat", "b", "slopsearch")

        results = db.get_searches_by_chat("chat", mode="monitor")
        assert len(results) == 1
        assert results[0]["name"] == "a"

    def test_get_searches_by_chat_empty(self):
        assert db.get_searches_by_chat("nope") == []

    # --- update_search ---

    def test_update_search_simple(self):
        sid = db.create_search("c", "n", "monitor")
        db.update_search(sid, name="renamed", max_price=99.9)
        s = db.get_search(sid)
        assert s["name"] == "renamed"
        assert s["max_price"] == 99.9

    def test_update_search_keywords_list(self):
        sid = db.create_search("c", "n", "monitor")
        db.update_search(sid, keywords=["a", "b"])
        s = db.get_search(sid)
        assert s["keywords"] == ["a", "b"]

    def test_update_search_products_list(self):
        sid = db.create_search("c", "n", "monitor")
        db.update_search(sid, products=["X", "Y"])
        s = db.get_search(sid)
        assert s["products"] == ["X", "Y"]

    def test_update_search_custom_filters_dict(self):
        sid = db.create_search("c", "n", "monitor")
        db.update_search(sid, custom_filters={"key": "val"})
        s = db.get_search(sid)
        assert json.loads(s["custom_filters"]) == {"key": "val"}

    def test_update_search_no_kwargs(self):
        """No-op when called with no kwargs."""
        sid = db.create_search("c", "n", "monitor")
        db.update_search(sid)  # should not raise

    # --- update_search_status ---

    def test_update_search_status(self):
        sid = db.create_search("c", "n", "monitor")
        db.update_search_status(sid, "monitoring")
        assert db.get_search(sid)["status"] == "monitoring"

    # --- delete_search (cascade) ---

    def test_delete_search(self):
        sid = db.create_search("c", "n", "cheap")
        # Add related data
        db.add_search_urls(sid, [{"url": "https://x", "product_name": "P"}])
        db.mark_seen(sid, ["lst1"])
        db.save_listing({"id": "lst1", "title": "T"})
        db.add_search_listing(sid, "lst1")

        db.delete_search(sid)

        assert db.get_search(sid) is None
        assert db.get_search_urls(sid) == []
        assert db.get_seen_ids(sid) == set()
        assert db.get_search_listings(sid) == []

    def test_delete_nonexistent(self):
        """Deleting a nonexistent search should not raise."""
        db.delete_search(9999)


# ---------------------------------------------------------------------------
# search_urls
# ---------------------------------------------------------------------------


class TestSearchUrls:
    def _make_search(self):
        return db.create_search("c", "n", "cheap")

    def test_add_and_get(self):
        sid = self._make_search()
        db.add_search_urls(
            sid,
            [
                {"url": "https://a", "product_name": "A"},
                {"url": "https://b"},
            ],
        )
        urls = db.get_search_urls(sid)
        assert len(urls) == 2
        u_map = {u["url"]: u for u in urls}
        assert u_map["https://a"]["product_name"] == "A"
        assert u_map["https://b"]["product_name"] is None

    def test_replace(self):
        sid = self._make_search()
        db.add_search_urls(sid, [{"url": "https://old", "product_name": "O"}])
        db.replace_search_urls(
            sid,
            [
                {"url": "https://new1", "product_name": "N1"},
                {"url": "https://new2", "product_name": "N2"},
            ],
        )
        urls = db.get_search_urls(sid)
        assert len(urls) == 2
        assert {u["url"] for u in urls} == {"https://new1", "https://new2"}

    def test_add_single(self):
        sid = self._make_search()
        db.add_search_url(sid, "https://single", "SP")
        urls = db.get_search_urls(sid)
        assert len(urls) == 1
        assert urls[0]["url"] == "https://single"
        assert urls[0]["product_name"] == "SP"

    def test_delete_single(self):
        sid = self._make_search()
        db.add_search_url(sid, "https://del", "D")
        url_id = db.get_search_urls(sid)[0]["id"]
        db.delete_search_url(url_id)
        assert db.get_search_urls(sid) == []

    def test_update_url(self):
        sid = self._make_search()
        db.add_search_url(sid, "https://old", "P")
        url_id = db.get_search_urls(sid)[0]["id"]
        db.update_search_url(url_id, "https://updated")
        urls = db.get_search_urls(sid)
        assert urls[0]["url"] == "https://updated"


# ---------------------------------------------------------------------------
# seen_listings
# ---------------------------------------------------------------------------


class TestSeenListings:
    def _make_search(self):
        return db.create_search("c", "n", "monitor")

    def test_mark_and_is_seen(self):
        sid = self._make_search()
        assert not db.is_seen(sid, "L1")
        db.mark_seen(sid, ["L1", "L2"])
        assert db.is_seen(sid, "L1")
        assert db.is_seen(sid, "L2")
        assert not db.is_seen(sid, "L3")

    def test_mark_seen_idempotent(self):
        sid = self._make_search()
        db.mark_seen(sid, ["L1"])
        db.mark_seen(sid, ["L1"])  # INSERT OR IGNORE — should not raise
        assert db.is_seen(sid, "L1")

    def test_get_seen_ids(self):
        sid = self._make_search()
        db.mark_seen(sid, ["A", "B", "C"])
        assert db.get_seen_ids(sid) == {"A", "B", "C"}

    def test_get_seen_ids_empty(self):
        sid = self._make_search()
        assert db.get_seen_ids(sid) == set()

    def test_clear_seen(self):
        sid = self._make_search()
        db.mark_seen(sid, ["A", "B"])
        db.clear_seen(sid)
        assert db.get_seen_ids(sid) == set()
        assert not db.is_seen(sid, "A")


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------


class TestListings:
    def test_save_and_get(self):
        listing = {
            "id": "abc123",
            "title": "Dell XPS 15",
            "price": "3500 zł",
            "url": "https://olx.pl/abc123",
            "description": "Great condition",
            "condition": "used",
            "location": "Gdańsk",
            "initial_price": 4000.0,
        }
        db.save_listing(listing)
        got = db.get_listing("abc123")
        assert got is not None
        assert got["listing_id"] == "abc123"
        assert got["title"] == "Dell XPS 15"
        assert got["price"] == "3500 zł"
        assert got["url"] == "https://olx.pl/abc123"
        assert got["description"] == "Great condition"
        assert got["condition"] == "used"
        assert got["location"] == "Gdańsk"
        assert got["initial_price"] == 4000.0

    def test_save_minimal(self):
        db.save_listing({"id": "min1"})
        got = db.get_listing("min1")
        assert got["listing_id"] == "min1"
        assert got["title"] is None

    def test_upsert(self):
        db.save_listing({"id": "u1", "title": "Old"})
        db.save_listing({"id": "u1", "title": "New"})
        assert db.get_listing("u1")["title"] == "New"

    def test_get_nonexistent(self):
        assert db.get_listing("nope") is None


# ---------------------------------------------------------------------------
# search_listings
# ---------------------------------------------------------------------------


class TestSearchListings:
    def _setup(self):
        """Create a search and save some listings."""
        sid = db.create_search("c", "n", "slopsearch")
        for lid in ("L1", "L2", "L3"):
            db.save_listing({"id": lid, "title": f"Title {lid}", "price": "100 zł"})
        return sid

    def test_add_and_get(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1")
        db.add_search_listing(sid, "L2", status="accepted", ai_summary="Good deal")

        all_listings = db.get_search_listings(sid)
        assert len(all_listings) == 2

        pending = db.get_search_listings(sid, status="pending")
        assert len(pending) == 1
        assert pending[0]["listing_id"] == "L1"

        accepted = db.get_search_listings(sid, status="accepted")
        assert len(accepted) == 1
        assert accepted[0]["listing_id"] == "L2"
        assert accepted[0]["ai_summary"] == "Good deal"

    def test_add_idempotent(self):
        """INSERT OR IGNORE — adding the same listing twice is a no-op."""
        sid = self._setup()
        db.add_search_listing(sid, "L1", status="pending")
        db.add_search_listing(sid, "L1", status="accepted")  # ignored
        lst = db.get_search_listings(sid, status="pending")
        assert len(lst) == 1  # still pending

    def test_update_search_listing(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1")
        db.update_search_listing(sid, "L1", status="accepted", ai_summary="Nice")
        lst = db.get_search_listings(sid, status="accepted")
        assert len(lst) == 1
        assert lst[0]["ai_summary"] == "Nice"

    def test_update_search_listing_no_kwargs(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1")
        db.update_search_listing(sid, "L1")  # no-op, should not raise

    def test_count_search_listings(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1", status="pending")
        db.add_search_listing(sid, "L2", status="pending")
        db.add_search_listing(sid, "L3", status="accepted")
        assert db.count_search_listings(sid, "pending") == 2
        assert db.count_search_listings(sid, "accepted") == 1
        assert db.count_search_listings(sid, "declined") == 0

    def test_get_next_pending_listing_fifo(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1")
        db.add_search_listing(sid, "L2")
        db.add_search_listing(sid, "L3")

        first = db.get_next_pending_listing(sid)
        assert first is not None
        assert first["listing_id"] == "L1"
        assert first["title"] == "Title L1"

    def test_get_next_pending_listing_none(self):
        sid = self._setup()
        assert db.get_next_pending_listing(sid) is None

    def test_get_next_pending_skips_non_pending(self):
        sid = self._setup()
        db.add_search_listing(sid, "L1", status="accepted")
        db.add_search_listing(sid, "L2", status="pending")
        nxt = db.get_next_pending_listing(sid)
        assert nxt["listing_id"] == "L2"

    def test_joined_fields(self):
        """get_search_listings joins listing data."""
        sid = self._setup()
        db.save_listing(
            {
                "id": "J1",
                "title": "Joined",
                "price": "50 zł",
                "url": "https://olx.pl/j1",
                "description": "desc",
                "condition": "new",
                "location": "Warsaw",
            }
        )
        db.add_search_listing(sid, "J1")
        lst = db.get_search_listings(sid)
        item = [x for x in lst if x["listing_id"] == "J1"][0]
        assert item["title"] == "Joined"
        assert item["price"] == "50 zł"
        assert item["location"] == "Warsaw"
