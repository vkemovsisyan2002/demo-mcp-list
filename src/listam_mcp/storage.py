"""SQLite storage for saved searches and the listing IDs already seen."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_search (
    name         TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_checked TEXT
);
CREATE TABLE IF NOT EXISTS seen_listing (
    search_name TEXT NOT NULL REFERENCES saved_search(name) ON DELETE CASCADE,
    listing_id  TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (search_name, listing_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "state.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def save_search(self, name: str, url: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO saved_search(name, url, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET url = excluded.url",
                (name, url, _now()),
            )

    def get_search(self, name: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM saved_search WHERE name = ?", (name,)).fetchone()

    def list_searches(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT s.*, COUNT(l.listing_id) AS seen_count FROM saved_search s "
            "LEFT JOIN seen_listing l ON l.search_name = s.name GROUP BY s.name ORDER BY s.name"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_search(self, name: str) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM saved_search WHERE name = ?", (name,)).rowcount > 0

    def mark_seen(self, name: str, listing_ids: list[str]) -> set[str]:
        """Record IDs and return the ones that were not seen before."""
        existing = {
            r[0]
            for r in self.db.execute(
                "SELECT listing_id FROM seen_listing WHERE search_name = ?", (name,)
            )
        }
        new = [i for i in listing_ids if i not in existing]
        now = _now()
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO seen_listing VALUES (?, ?, ?)",
                [(name, i, now) for i in new],
            )
            self.db.execute(
                "UPDATE saved_search SET last_checked = ? WHERE name = ?", (now, name)
            )
        return set(new)
