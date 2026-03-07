"""
OLX Scraper — SQLite Data Layer

All tables use ON DELETE CASCADE so that deleting a search
automatically purges every related row.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from config import CONFIG

# ============================================================================
# CONNECTION HELPERS
# ============================================================================

_DB_PATH = CONFIG.DB_PATH


def _connect() -> sqlite3.Connection:
    os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for DB transactions."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# SCHEMA
# ============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id              INTEGER PRIMARY KEY,
    chat_id         TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    mode            TEXT    NOT NULL CHECK(mode IN ('monitor','slopsearch','cheap')),
    status          TEXT    NOT NULL DEFAULT 'active',
    url             TEXT,
    max_price       REAL,
    keywords        TEXT,           -- JSON array
    original_query  TEXT,
    products        TEXT,           -- JSON array
    location        TEXT,           -- city slug e.g. "gdansk"
    location_radius INTEGER,        -- search radius in km
    min_price       REAL,
    base_path       TEXT,           -- OLX category path e.g. "elektronika/komputery"
    condition       TEXT,           -- "new", "used", or NULL
    browse_category TEXT,           -- leaf subcategory for keyword-less browse
    custom_filters  TEXT,           -- JSON dict of extra OLX search params e.g. {"enginesize:to": 125}
    created_at      INTEGER NOT NULL,
    UNIQUE(chat_id, name)
);

CREATE TABLE IF NOT EXISTS search_urls (
    id              INTEGER PRIMARY KEY,
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    url             TEXT    NOT NULL,
    product_name    TEXT
);

CREATE TABLE IF NOT EXISTS seen_listings (
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    listing_id      TEXT    NOT NULL,
    first_seen      INTEGER NOT NULL,
    PRIMARY KEY(search_id, listing_id)
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id      TEXT    PRIMARY KEY,
    title           TEXT,
    price           TEXT,
    url             TEXT,
    description     TEXT,
    condition       TEXT,
    location        TEXT,
    initial_price   REAL
);

CREATE TABLE IF NOT EXISTS search_listings (
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    listing_id      TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    ai_summary      TEXT,
    decline_feedback TEXT,
    first_seen      INTEGER,
    last_seen       INTEGER,
    removed_at      INTEGER,
    PRIMARY KEY(search_id, listing_id)
);

CREATE TABLE IF NOT EXISTS sent_messages (
    message_id      TEXT    NOT NULL,
    chat_id         TEXT    NOT NULL,
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    listing_id      TEXT    NOT NULL,
    product_name    TEXT,
    PRIMARY KEY(message_id, chat_id, listing_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY,
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    listing_title   TEXT,
    product         TEXT,
    feedback        TEXT,
    created_at      INTEGER
);

CREATE TABLE IF NOT EXISTS user_prefs (
    chat_id         TEXT PRIMARY KEY,
    mode            TEXT NOT NULL DEFAULT 'monitor',
    override_language TEXT
);
"""


def init_db() -> None:
    """Create tables if they don't exist, migrate existing schemas."""
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        # Migrations for existing DBs
        cols = {r[1] for r in conn.execute("PRAGMA table_info(searches)").fetchall()}
        if "location" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN location TEXT")
        if "location_radius" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN location_radius INTEGER")
        if "base_path" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN base_path TEXT")
        if "condition" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN condition TEXT")
        if "min_price" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN min_price REAL")
        if "browse_category" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN browse_category TEXT")
        if "custom_filters" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN custom_filters TEXT")
            
        cols_prefs = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        if "override_language" not in cols_prefs:
            conn.execute("ALTER TABLE user_prefs ADD COLUMN override_language TEXT")


# ============================================================================
# SEARCHES
# ============================================================================

def create_search(
    chat_id: str,
    name: str,
    mode: str,
    *,
    url: str = None,
    max_price: float = None,
    min_price: float = None,
    keywords: list = None,
    original_query: str = None,
    products: list = None,
    location: str = None,
    location_radius: int = None,
    base_path: str = None,
    condition: str = None,
    browse_category: str = None,
    custom_filters: dict = None,
    status: str = "active",
) -> int:
    """Create a new search entry. Returns the search id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO searches
               (chat_id, name, mode, status, url, max_price, min_price, keywords,
                original_query, products, location, location_radius,
                base_path, condition, browse_category, custom_filters, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id, name, mode, status, url, max_price, min_price,
                json.dumps(keywords) if keywords else None,
                original_query,
                json.dumps(products) if products else None,
                location,
                location_radius,
                base_path,
                condition,
                browse_category,
                json.dumps(custom_filters) if custom_filters else None,
                int(time.time()),
            ),
        )
        return cur.lastrowid


def get_search(search_id: int) -> Optional[dict]:
    """Get a single search by id."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        return _row_to_dict(row) if row else None


def get_search_by_name(chat_id: str, name: str) -> Optional[dict]:
    """Get a search by chat_id and name."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM searches WHERE chat_id = ? AND name = ?",
            (chat_id, name),
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_searches_by_chat(chat_id: str, mode: str = None) -> list[dict]:
    """Get all searches for a chat, optionally filtered by mode."""
    with get_db() as conn:
        if mode:
            rows = conn.execute(
                "SELECT * FROM searches WHERE chat_id = ? AND mode = ?",
                (chat_id, mode),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM searches WHERE chat_id = ?", (chat_id,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_active_searches() -> list[dict]:
    """Get all searches that should be scraped (active or monitoring)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM searches WHERE status IN ('active','pending_scrape','monitoring')"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def update_search_status(search_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE searches SET status = ? WHERE id = ?", (status, search_id))


def update_search(search_id: int, **kwargs) -> None:
    """Update arbitrary fields on a search."""
    if not kwargs:
        return
    # Serialize list/dict fields
    for key in ("keywords", "products"):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])
    if "custom_filters" in kwargs and isinstance(kwargs["custom_filters"], dict):
        kwargs["custom_filters"] = json.dumps(kwargs["custom_filters"])
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [search_id]
    with get_db() as conn:
        conn.execute(f"UPDATE searches SET {cols} WHERE id = ?", vals)


def delete_search(search_id: int) -> None:
    """Delete a search — CASCADE removes all related data."""
    with get_db() as conn:
        conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))


# ============================================================================
# SEARCH URLS (cheap mode product URLs)
# ============================================================================

def add_search_urls(search_id: int, url_entries: list[dict]) -> None:
    """Add product URLs for a cheap mode search. Each entry: {url, product_name}."""
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO search_urls (search_id, url, product_name) VALUES (?, ?, ?)",
            [(search_id, e["url"], e.get("product_name")) for e in url_entries],
        )


def get_search_urls(search_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM search_urls WHERE search_id = ?", (search_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def replace_search_urls(search_id: int, url_entries: list[dict]) -> None:
    """Replace all product URLs for a search."""
    with get_db() as conn:
        conn.execute("DELETE FROM search_urls WHERE search_id = ?", (search_id,))
        conn.executemany(
            "INSERT INTO search_urls (search_id, url, product_name) VALUES (?, ?, ?)",
            [(search_id, e["url"], e.get("product_name")) for e in url_entries],
        )


def add_search_url(search_id: int, url: str, product_name: str) -> None:
    """Add a single product URL to a search."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO search_urls (search_id, url, product_name) VALUES (?, ?, ?)",
            (search_id, url, product_name),
        )


def delete_search_url(url_id: int) -> None:
    """Delete a single search URL by its ID."""
    with get_db() as conn:
        conn.execute("DELETE FROM search_urls WHERE id = ?", (url_id,))


def update_search_url(url_id: int, url: str) -> None:
    """Update a search URL's URL field."""
    with get_db() as conn:
        conn.execute("UPDATE search_urls SET url = ? WHERE id = ?", (url, url_id))


# ============================================================================
# SEEN LISTINGS
# ============================================================================

def is_seen(search_id: int, listing_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_listings WHERE search_id = ? AND listing_id = ?",
            (search_id, listing_id),
        ).fetchone()
        return row is not None


def mark_seen(search_id: int, listing_ids: list[str]) -> None:
    now = int(time.time())
    with get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_listings (search_id, listing_id, first_seen) VALUES (?, ?, ?)",
            [(search_id, lid, now) for lid in listing_ids],
        )


def get_seen_ids(search_id: int) -> set[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT listing_id FROM seen_listings WHERE search_id = ?", (search_id,)
        ).fetchall()
        return {r["listing_id"] for r in rows}


def clear_seen(search_id: int) -> None:
    """Remove all seen listings for a search (e.g. after editing params)."""
    with get_db() as conn:
        conn.execute("DELETE FROM seen_listings WHERE search_id = ?", (search_id,))


# ============================================================================
# LISTINGS (shared cache)
# ============================================================================

def save_listing(listing: dict) -> None:
    """Upsert a listing into the shared cache."""
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO listings
               (listing_id, title, price, url, description, condition, location, initial_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing["id"],
                listing.get("title"),
                listing.get("price"),
                listing.get("url"),
                listing.get("description"),
                listing.get("condition"),
                listing.get("location"),
                listing.get("initial_price"),
            ),
        )


def get_listing(listing_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        return dict(row) if row else None


# ============================================================================
# SEARCH LISTINGS (per-search status: pending/accepted/declined/sent)
# ============================================================================

def add_search_listing(search_id: int, listing_id: str, status: str = "pending", **kwargs) -> None:
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO search_listings
               (search_id, listing_id, status, ai_summary, decline_feedback, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, listing_id, status,
                kwargs.get("ai_summary"),
                kwargs.get("decline_feedback"),
                now, now,
            ),
        )


def update_search_listing(search_id: int, listing_id: str, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [search_id, listing_id]
    with get_db() as conn:
        conn.execute(
            f"UPDATE search_listings SET {cols} WHERE search_id = ? AND listing_id = ?",
            vals,
        )


def get_search_listings(search_id: int, status: str = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT sl.*, l.title, l.price, l.url, l.description, l.condition, l.location "
                "FROM search_listings sl JOIN listings l ON sl.listing_id = l.listing_id "
                "WHERE sl.search_id = ? AND sl.status = ?",
                (search_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT sl.*, l.title, l.price, l.url, l.description, l.condition, l.location "
                "FROM search_listings sl JOIN listings l ON sl.listing_id = l.listing_id "
                "WHERE sl.search_id = ?",
                (search_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def count_search_listings(search_id: int, status: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM search_listings WHERE search_id = ? AND status = ?",
            (search_id, status),
        ).fetchone()
        return row["cnt"]


def get_next_pending_listing(search_id: int) -> Optional[dict]:
    """Get the next pending listing for review (FIFO)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT sl.*, l.title, l.price, l.url, l.description, l.condition, l.location "
            "FROM search_listings sl JOIN listings l ON sl.listing_id = l.listing_id "
            "WHERE sl.search_id = ? AND sl.status = 'pending' "
            "ORDER BY sl.first_seen ASC LIMIT 1",
            (search_id,),
        ).fetchone()
        return dict(row) if row else None


def mark_active_listings_sold(search_id: int, live_ids: set[str]) -> list[dict]:
    """Mark listings not in live_ids as sold. Returns newly sold listings."""
    now = int(time.time())
    with get_db() as conn:
        active = conn.execute(
            "SELECT sl.listing_id, l.title FROM search_listings sl "
            "JOIN listings l ON sl.listing_id = l.listing_id "
            "WHERE sl.search_id = ? AND sl.status = 'accepted'",
            (search_id,),
        ).fetchall()

        sold = []
        for row in active:
            if row["listing_id"] not in live_ids:
                conn.execute(
                    "UPDATE search_listings SET status = 'sold', removed_at = ? "
                    "WHERE search_id = ? AND listing_id = ?",
                    (now, search_id, row["listing_id"]),
                )
                sold.append(dict(row))

        # Update last_seen for still-active ones
        for row in active:
            if row["listing_id"] in live_ids:
                conn.execute(
                    "UPDATE search_listings SET last_seen = ? "
                    "WHERE search_id = ? AND listing_id = ?",
                    (now, search_id, row["listing_id"]),
                )

        return sold


# ============================================================================
# SENT MESSAGES (Telegram message_id → listing mapping)
# ============================================================================

def record_sent_message(
    message_id: str, chat_id: str, search_id: int,
    listing_id: str, product_name: str = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sent_messages (message_id, chat_id, search_id, listing_id, product_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, chat_id, search_id, listing_id, product_name),
        )


def lookup_sent_message(message_id: str, chat_id: str) -> Optional[dict]:
    """Look up the first sent message entry for a given message_id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT sm.*, l.title, l.price, l.url "
            "FROM sent_messages sm LEFT JOIN listings l ON sm.listing_id = l.listing_id "
            "WHERE sm.message_id = ? AND sm.chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        return dict(row) if row else None


def get_sent_messages_by_msg_id(message_id: str, chat_id: str) -> list[dict]:
    """Get all sent message entries for a grouped message (one per listing)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sent_messages WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# FEEDBACK
# ============================================================================

def add_feedback(search_id: int, listing_title: str, product: str, feedback_text: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO feedback (search_id, listing_title, product, feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (search_id, listing_title, product, feedback_text, int(time.time())),
        )


def get_feedback(search_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE search_id = ? ORDER BY created_at", (search_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# USER PREFERENCES
# ============================================================================

def get_user_mode(chat_id: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT mode FROM user_prefs WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["mode"] if row else "monitor"


def set_user_mode(chat_id: str, mode: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_prefs (chat_id, mode) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode",
            (chat_id, mode),
        )


def get_user_language(chat_id: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT override_language FROM user_prefs WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["override_language"] if row else None


def set_user_language(chat_id: str, lang: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_prefs (chat_id, override_language) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET override_language=excluded.override_language",
            (chat_id, lang),
        )


# ============================================================================
# HELPERS
# ============================================================================

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a Row to dict, deserializing JSON fields."""
    d = dict(row)
    for key in ("keywords", "products"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
