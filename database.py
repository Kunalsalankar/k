"""SQLite storage. Scraping code does not import this module's SQL."""

import sqlite3
from datetime import datetime, timezone

import config


def connect(path=None):
    config.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    database_path = path or config.DATABASE_PATH
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialise(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            state_name TEXT NOT NULL,
            district_name TEXT NOT NULL,
            nic_filter TEXT,
            portal_msme_count INTEGER NOT NULL,
            collected_count INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nic_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            state_name TEXT NOT NULL,
            district_name TEXT NOT NULL,
            nic_code TEXT NOT NULL,
            nic_description TEXT NOT NULL,
            portal_count INTEGER NOT NULL,
            collected_count INTEGER NOT NULL,
            detail_url TEXT NOT NULL UNIQUE,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS enterprises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enterprise_name TEXT NOT NULL,
            business_address TEXT,
            state_name TEXT,
            district_name TEXT,
            pin_code TEXT,
            nic_code TEXT,
            nic_description TEXT,
            search_keyword TEXT,
            source_url TEXT,
            collection_date TEXT,
            verification_status TEXT,
            dedupe_key TEXT NOT NULL UNIQUE
        );
        """
    )
    connection.commit()


def save_collection(connection, keyword, state_name, district_name, nic_filter, summaries, records):
    """Persist one search. Existing enterprise rows with the same key are kept."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    portal_total = sum(item["portal_count"] for item in summaries)
    connection.execute(
        """
        INSERT INTO search_runs (
            keyword, state_name, district_name, nic_filter,
            portal_msme_count, collected_count, source_url, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            keyword,
            state_name,
            district_name,
            nic_filter or "",
            portal_total,
            len(records),
            config.SEARCH_URL,
            now,
        ),
    )
    for item in summaries:
        connection.execute(
            """
            INSERT INTO nic_summaries (
                keyword, state_name, district_name, nic_code, nic_description,
                portal_count, collected_count, detail_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(detail_url) DO UPDATE SET
                portal_count = excluded.portal_count,
                collected_count = excluded.collected_count,
                nic_description = excluded.nic_description,
                updated_at = excluded.updated_at
            """,
            (
                keyword,
                state_name,
                district_name,
                item["nic_code"],
                item["nic_description"],
                item["portal_count"],
                item["collected_count"],
                item["detail_url"],
                now,
            ),
        )
    inserted = 0
    for record in records:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO enterprises (
                enterprise_name, business_address, state_name, district_name,
                pin_code, nic_code, nic_description, search_keyword, source_url,
                collection_date, verification_status, dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["Enterprise Name"],
                record["Business Address"],
                record["State"],
                record["District"],
                record["PIN Code"],
                record["NIC Code"],
                record["NIC Description"],
                record["Search Keyword"],
                record["Source URL"],
                record["Collection Date"],
                record["Verification Status"],
                record["dedupe_key"],
            ),
        )
        inserted += cursor.rowcount
    connection.commit()
    return {"portal_msme_count": portal_total, "inserted": inserted}


def fetch_enterprises(connection, state="", district="", keyword="", nic_code="", text=""):
    clauses = []
    params = []
    if state:
        clauses.append("state_name = ?")
        params.append(state)
    if district:
        clauses.append("district_name = ?")
        params.append(district)
    if keyword:
        clauses.append("search_keyword = ?")
        params.append(keyword)
    if nic_code:
        clauses.append("nic_code = ?")
        params.append(nic_code)
    if text:
        clauses.append(
            "(enterprise_name LIKE ? OR business_address LIKE ? OR nic_description LIKE ?)"
        )
        like = f"%{text}%"
        params.extend([like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT enterprise_name, business_address, state_name, district_name,
               pin_code, nic_code, nic_description, search_keyword, source_url,
               collection_date, verification_status
        FROM enterprises
        {where}
        ORDER BY state_name, district_name, enterprise_name
        """,
        params,
    ).fetchall()
    return rows


def fetch_nic_summaries(connection, state="", district="", keyword="", nic_code=""):
    clauses = []
    params = []
    if state:
        clauses.append("state_name = ?")
        params.append(state)
    if district:
        clauses.append("district_name = ?")
        params.append(district)
    if keyword:
        clauses.append("keyword = ? COLLATE NOCASE")
        params.append(keyword)
    if nic_code:
        clauses.append("nic_code = ?")
        params.append(nic_code)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return connection.execute(
        f"""
        SELECT keyword, state_name, district_name, nic_code, nic_description,
               portal_count, collected_count, detail_url
        FROM nic_summaries
        {where}
        ORDER BY nic_code
        """,
        params,
    ).fetchall()


def distinct_values(connection, column):
    allowed = {
        "state_name": "state_name",
        "district_name": "district_name",
        "search_keyword": "search_keyword",
        "nic_code": "nic_code",
    }
    if column not in allowed:
        raise ValueError(f"Unsupported column: {column}")
    rows = connection.execute(
        f"""
        SELECT DISTINCT {allowed[column]} AS value
        FROM enterprises
        WHERE {allowed[column]} IS NOT NULL AND {allowed[column]} != ''
        ORDER BY value
        """
    ).fetchall()
    return [row["value"] for row in rows]
