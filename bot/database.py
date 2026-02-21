"""
SQLite database layer.
Handles schema creation, migrations, and all CRUD operations for:
  - followed_users  (core tracking)
  - activity_log    (audit trail)
  - bot_config      (runtime configuration)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import config as cfg

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection(db_path: Path | None = None):
    db = db_path or cfg.DB_PATH
    _ensure_dir(db)
    conn = sqlite3.connect(str(db), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema & migrations
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS followed_users (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_user_id       TEXT    UNIQUE NOT NULL,
    username                TEXT    NOT NULL,
    full_name               TEXT,
    source_account          TEXT    NOT NULL,
    is_private              INTEGER NOT NULL DEFAULT 0,
    status                  TEXT    NOT NULL DEFAULT 'following',
    followed_at             TEXT    NOT NULL,
    request_accepted_at     TEXT,
    follow_back_detected_at TEXT,
    unfollowed_at           TEXT,
    likes_given             INTEGER NOT NULL DEFAULT 0,
    stories_viewed          INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fu_status      ON followed_users(status);
CREATE INDEX IF NOT EXISTS idx_fu_source      ON followed_users(source_account);
CREATE INDEX IF NOT EXISTS idx_fu_followed_at ON followed_users(followed_at);

CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    target_username TEXT,
    details         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_al_action     ON activity_log(action);
CREATE INDEX IF NOT EXISTS idx_al_created_at ON activity_log(created_at);

CREATE TABLE IF NOT EXISTS bot_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT
);
"""


def init_db(db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        _seed_default_config(conn)


def _seed_default_config(conn: sqlite3.Connection) -> None:
    defaults: list[tuple[str, str, str]] = [
        ("daily_follow_limit", str(cfg.DAILY_FOLLOW_LIMIT), "Max follows per day"),
        ("daily_unfollow_limit", str(cfg.DAILY_UNFOLLOW_LIMIT), "Max unfollows per day"),
        ("hourly_follow_limit", str(cfg.HOURLY_FOLLOW_LIMIT), "Max follows per hour"),
        ("hourly_unfollow_limit", str(cfg.HOURLY_UNFOLLOW_LIMIT), "Max unfollows per hour"),
        ("follow_back_check_days", str(cfg.FOLLOW_BACK_CHECK_DAYS), "Days before checking follow-back"),
        ("unfollow_after_days", str(cfg.UNFOLLOW_AFTER_DAYS), "Days before auto-unfollowing non-followers"),
        ("pending_request_timeout_days", str(cfg.PENDING_REQUEST_TIMEOUT_DAYS), "Days before withdrawing unanswered follow request"),
        ("active_hours_start", str(cfg.ACTIVE_HOURS_START), "Bot active from (hour, 0-23)"),
        ("active_hours_end", str(cfg.ACTIVE_HOURS_END), "Bot active until (hour, 0-23)"),
        ("warmup_enabled", str(cfg.WARMUP_ENABLED), "Enable warm-up mode (True/False)"),
        ("like_chance_min", str(cfg.LIKE_CHANCE_MIN), "Min probability of liking when following"),
        ("like_chance_max", str(cfg.LIKE_CHANCE_MAX), "Max probability of liking when following"),
        ("story_view_chance_min", str(cfg.STORY_VIEW_CHANCE_MIN), "Min probability of viewing story"),
        ("story_view_chance_max", str(cfg.STORY_VIEW_CHANCE_MAX), "Max probability of viewing story"),
        ("drift_period_min", str(cfg.DRIFT_PERIOD_MIN), "Min actions before re-rolling probability"),
        ("drift_period_max", str(cfg.DRIFT_PERIOD_MAX), "Max actions before re-rolling probability"),
    ]
    for key, value, desc in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO bot_config (key, value, description) VALUES (?, ?, ?)",
            (key, value, desc),
        )


# ---------------------------------------------------------------------------
# bot_config helpers
# ---------------------------------------------------------------------------

def get_config(key: str, default: str | None = None, db_path: Path | None = None) -> str | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def get_config_int(key: str, default: int = 0, **kw: Any) -> int:
    val = get_config(key, **kw)
    return int(val) if val is not None else default


def get_config_float(key: str, default: float = 0.0, **kw: Any) -> float:
    val = get_config(key, **kw)
    return float(val) if val is not None else default


def get_config_bool(key: str, default: bool = False, **kw: Any) -> bool:
    val = get_config(key, **kw)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def set_config(key: str, value: str, description: str | None = None, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        if description:
            conn.execute(
                "INSERT INTO bot_config (key, value, description) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, description=excluded.description",
                (key, value, description),
            )
        else:
            conn.execute(
                "INSERT INTO bot_config (key, value, description) VALUES (?, ?, '') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


def get_all_config(db_path: Path | None = None) -> list[dict[str, str]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT key, value, description FROM bot_config ORDER BY key").fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# followed_users CRUD
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def add_followed_user(
    instagram_user_id: str,
    username: str,
    source_account: str,
    is_private: bool = False,
    full_name: str | None = None,
    status: str = "following",
    db_path: Path | None = None,
) -> int:
    now = _now()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO followed_users
               (instagram_user_id, username, full_name, source_account,
                is_private, status, followed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (instagram_user_id, username, full_name, source_account,
             int(is_private), status, now, now),
        )
        return cur.lastrowid  # type: ignore[return-value]


def user_exists(instagram_user_id: str, db_path: Path | None = None) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM followed_users WHERE instagram_user_id = ?",
            (instagram_user_id,),
        ).fetchone()
        return row is not None


def get_user_by_ig_id(instagram_user_id: str, db_path: Path | None = None) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM followed_users WHERE instagram_user_id = ?",
            (instagram_user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_user_status(
    instagram_user_id: str,
    new_status: str,
    db_path: Path | None = None,
    **extra_fields: Any,
) -> None:
    sets = ["status = ?"]
    params: list[Any] = [new_status]

    timestamp_map = {
        "following": "request_accepted_at",
        "followed_back": "follow_back_detected_at",
        "unfollowed": "unfollowed_at",
    }
    ts_col = timestamp_map.get(new_status)
    if ts_col:
        sets.append(f"{ts_col} = ?")
        params.append(_now())

    for col, val in extra_fields.items():
        sets.append(f"{col} = ?")
        params.append(val)

    params.append(instagram_user_id)
    sql = f"UPDATE followed_users SET {', '.join(sets)} WHERE instagram_user_id = ?"
    with get_connection(db_path) as conn:
        conn.execute(sql, params)


def increment_likes(instagram_user_id: str, count: int = 1, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE followed_users SET likes_given = likes_given + ? WHERE instagram_user_id = ?",
            (count, instagram_user_id),
        )


def increment_stories(instagram_user_id: str, count: int = 1, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE followed_users SET stories_viewed = stories_viewed + ? WHERE instagram_user_id = ?",
            (count, instagram_user_id),
        )


# ---------------------------------------------------------------------------
# Query helpers for the engine
# ---------------------------------------------------------------------------

def get_users_to_check_followback(db_path: Path | None = None) -> list[dict]:
    """Users with status 'following' whose followed_at is older than FOLLOW_BACK_CHECK_DAYS."""
    days = get_config_int("follow_back_check_days", cfg.FOLLOW_BACK_CHECK_DAYS, db_path=db_path)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE status = 'following' AND followed_at <= ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_users_to_unfollow(db_path: Path | None = None) -> list[dict]:
    """Users whose follow-back window expired (status 'following', older than UNFOLLOW_AFTER_DAYS)."""
    days = get_config_int("unfollow_after_days", cfg.UNFOLLOW_AFTER_DAYS, db_path=db_path)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE status = 'following' AND followed_at <= ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_users_followed_back(db_path: Path | None = None) -> list[dict]:
    """Users who followed back and should be unfollowed now."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE status = 'followed_back'"
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_requests(db_path: Path | None = None) -> list[dict]:
    """Private account follow requests still pending."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE status = 'pending_request'"
        ).fetchall()
        return [dict(r) for r in rows]


def get_expired_pending_requests(db_path: Path | None = None) -> list[dict]:
    """Pending requests older than PENDING_REQUEST_TIMEOUT_DAYS."""
    days = get_config_int("pending_request_timeout_days", cfg.PENDING_REQUEST_TIMEOUT_DAYS, db_path=db_path)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE status = 'pending_request' AND followed_at <= ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stats / counts
# ---------------------------------------------------------------------------

def count_today_actions(action: str, db_path: Path | None = None) -> int:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE action = ? AND created_at >= ?",
            (action, today_start),
        ).fetchone()
        return row["cnt"] if row else 0


def count_hour_actions(action: str, db_path: Path | None = None) -> int:
    hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE action = ? AND created_at >= ?",
            (action, hour_ago),
        ).fetchone()
        return row["cnt"] if row else 0


def get_stats(db_path: Path | None = None) -> dict[str, int]:
    with get_connection(db_path) as conn:
        def _count(where: str) -> int:
            r = conn.execute(f"SELECT COUNT(*) as cnt FROM followed_users WHERE {where}").fetchone()
            return r["cnt"] if r else 0

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        total_followed = _count("1=1")
        active_following = _count("status = 'following'")
        followed_back = _count("status = 'followed_back'")
        unfollowed = _count("status = 'unfollowed'")
        pending_requests = _count("status = 'pending_request'")
        withdrawn = _count("status = 'request_withdrawn'")

        today_followed = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE action = 'follow' AND created_at >= ?",
            (today_start,),
        ).fetchone()["cnt"]
        today_unfollowed = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE action = 'unfollow' AND created_at >= ?",
            (today_start,),
        ).fetchone()["cnt"]

        return {
            "total_followed": total_followed,
            "active_following": active_following,
            "followed_back": followed_back,
            "unfollowed": unfollowed,
            "pending_requests": pending_requests,
            "request_withdrawn": withdrawn,
            "today_followed": today_followed,
            "today_unfollowed": today_unfollowed,
            "followback_ratio": round(followed_back / max(total_followed, 1) * 100, 1),
        }


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_activity(action: str, target_username: str = "", details: str = "", db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO activity_log (action, target_username, details, created_at) VALUES (?, ?, ?, ?)",
            (action, target_username, details, _now()),
        )


def get_recent_activity(limit: int = 50, db_path: Path | None = None) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
