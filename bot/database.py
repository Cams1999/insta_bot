"""
SQLite database layer with multi-account support.
Tables:
  - accounts         (Instagram account management)
  - followed_users   (core tracking, per account)
  - activity_log     (audit trail, per account)
  - bot_config       (runtime configuration, per account)
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


def _now() -> str:
    return datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ig_username     TEXT    UNIQUE NOT NULL,
    ig_password     TEXT    NOT NULL,
    ig_2fa_seed     TEXT    DEFAULT '',
    proxy_url       TEXT    DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'inactive',
    target_username TEXT    DEFAULT '',
    follow_count    INTEGER DEFAULT 100,
    created_at      TEXT    NOT NULL,
    last_active_at  TEXT
);

CREATE TABLE IF NOT EXISTS followed_users (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              INTEGER NOT NULL DEFAULT 0,
    instagram_user_id       TEXT    NOT NULL,
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
    created_at              TEXT    NOT NULL,
    UNIQUE(account_id, instagram_user_id)
);

CREATE INDEX IF NOT EXISTS idx_fu_account    ON followed_users(account_id);
CREATE INDEX IF NOT EXISTS idx_fu_status     ON followed_users(status);
CREATE INDEX IF NOT EXISTS idx_fu_source     ON followed_users(source_account);
CREATE INDEX IF NOT EXISTS idx_fu_followed   ON followed_users(followed_at);

CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL DEFAULT 0,
    action          TEXT NOT NULL,
    target_username TEXT,
    details         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_al_account    ON activity_log(account_id);
CREATE INDEX IF NOT EXISTS idx_al_action     ON activity_log(action);
CREATE INDEX IF NOT EXISTS idx_al_created_at ON activity_log(created_at);

CREATE TABLE IF NOT EXISTS bot_config (
    account_id  INTEGER NOT NULL DEFAULT 0,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    description TEXT,
    PRIMARY KEY (account_id, key)
);
"""


def init_db(db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


def seed_account_config(account_id: int, db_path: Path | None = None) -> None:
    defaults: list[tuple[str, str, str]] = [
        ("daily_follow_limit", str(cfg.DAILY_FOLLOW_LIMIT), "Max follows per day"),
        ("daily_unfollow_limit", str(cfg.DAILY_UNFOLLOW_LIMIT), "Max unfollows per day"),
        ("hourly_follow_limit", str(cfg.HOURLY_FOLLOW_LIMIT), "Max follows per hour"),
        ("hourly_unfollow_limit", str(cfg.HOURLY_UNFOLLOW_LIMIT), "Max unfollows per hour"),
        ("follow_back_check_days", str(cfg.FOLLOW_BACK_CHECK_DAYS), "Days before checking follow-back"),
        ("unfollow_after_days", str(cfg.UNFOLLOW_AFTER_DAYS), "Days before auto-unfollowing"),
        ("pending_request_timeout_days", str(cfg.PENDING_REQUEST_TIMEOUT_DAYS), "Days before withdrawing request"),
        ("active_hours_start", str(cfg.ACTIVE_HOURS_START), "Bot active from (hour)"),
        ("active_hours_end", str(cfg.ACTIVE_HOURS_END), "Bot active until (hour)"),
        ("warmup_enabled", str(cfg.WARMUP_ENABLED), "Enable warm-up mode"),
        ("like_chance_min", str(cfg.LIKE_CHANCE_MIN), "Min like probability"),
        ("like_chance_max", str(cfg.LIKE_CHANCE_MAX), "Max like probability"),
        ("story_view_chance_min", str(cfg.STORY_VIEW_CHANCE_MIN), "Min story view probability"),
        ("story_view_chance_max", str(cfg.STORY_VIEW_CHANCE_MAX), "Max story view probability"),
        ("drift_period_min", str(cfg.DRIFT_PERIOD_MIN), "Min actions before re-rolling"),
        ("drift_period_max", str(cfg.DRIFT_PERIOD_MAX), "Max actions before re-rolling"),
    ]
    with get_connection(db_path) as conn:
        for key, value, desc in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO bot_config (account_id, key, value, description) VALUES (?, ?, ?, ?)",
                (account_id, key, value, desc),
            )


# ---------------------------------------------------------------------------
# Accounts CRUD
# ---------------------------------------------------------------------------

def add_account(ig_username: str, ig_password: str, ig_2fa_seed: str = "",
                proxy_url: str = "", db_path: Path | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO accounts (ig_username, ig_password, ig_2fa_seed, proxy_url, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (ig_username, ig_password, ig_2fa_seed, proxy_url, _now()),
        )
        account_id = cur.lastrowid
    seed_account_config(account_id, db_path)
    return account_id


def update_account(account_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = [f"{k} = ?" for k in fields]
    vals = list(fields.values()) + [account_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", vals)


def delete_account(account_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM followed_users WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM activity_log WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM bot_config WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def get_account(account_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None


def get_all_accounts() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def set_account_status(account_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET status = ?, last_active_at = ? WHERE id = ?",
                      (status, _now(), account_id))


# ---------------------------------------------------------------------------
# bot_config helpers (per account)
# ---------------------------------------------------------------------------

def get_config(key: str, default: str | None = None, account_id: int = 0,
               db_path: Path | None = None) -> str | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE account_id = ? AND key = ?",
            (account_id, key),
        ).fetchone()
        return row["value"] if row else default


def get_config_int(key: str, default: int = 0, account_id: int = 0, **kw: Any) -> int:
    val = get_config(key, account_id=account_id, **kw)
    return int(val) if val is not None else default


def get_config_float(key: str, default: float = 0.0, account_id: int = 0, **kw: Any) -> float:
    val = get_config(key, account_id=account_id, **kw)
    return float(val) if val is not None else default


def get_config_bool(key: str, default: bool = False, account_id: int = 0, **kw: Any) -> bool:
    val = get_config(key, account_id=account_id, **kw)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def set_config(key: str, value: str, description: str | None = None,
               account_id: int = 0, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO bot_config (account_id, key, value, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, key) DO UPDATE SET value=excluded.value",
            (account_id, key, value, description or ""),
        )


def get_all_config(account_id: int = 0, db_path: Path | None = None) -> list[dict[str, str]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, description FROM bot_config WHERE account_id = ? ORDER BY key",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# followed_users CRUD (per account)
# ---------------------------------------------------------------------------

def add_followed_user(
    instagram_user_id: str, username: str, source_account: str,
    is_private: bool = False, full_name: str | None = None,
    status: str = "following", account_id: int = 0,
    db_path: Path | None = None,
) -> int:
    now = _now()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO followed_users
               (account_id, instagram_user_id, username, full_name, source_account,
                is_private, status, followed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, instagram_user_id, username, full_name, source_account,
             int(is_private), status, now, now),
        )
        return cur.lastrowid


def user_exists(instagram_user_id: str, account_id: int = 0, db_path: Path | None = None) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM followed_users WHERE account_id = ? AND instagram_user_id = ?",
            (account_id, instagram_user_id),
        ).fetchone()
        return row is not None


def update_user_status(instagram_user_id: str, new_status: str,
                       account_id: int = 0, db_path: Path | None = None,
                       **extra_fields: Any) -> None:
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

    params.extend([account_id, instagram_user_id])
    sql = f"UPDATE followed_users SET {', '.join(sets)} WHERE account_id = ? AND instagram_user_id = ?"
    with get_connection(db_path) as conn:
        conn.execute(sql, params)


def increment_likes(instagram_user_id: str, count: int = 1,
                    account_id: int = 0, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE followed_users SET likes_given = likes_given + ? "
            "WHERE account_id = ? AND instagram_user_id = ?",
            (count, account_id, instagram_user_id),
        )


def increment_stories(instagram_user_id: str, count: int = 1,
                      account_id: int = 0, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE followed_users SET stories_viewed = stories_viewed + ? "
            "WHERE account_id = ? AND instagram_user_id = ?",
            (count, account_id, instagram_user_id),
        )


# ---------------------------------------------------------------------------
# Query helpers (per account)
# ---------------------------------------------------------------------------

def get_users_to_check_followback(account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    days = get_config_int("follow_back_check_days", cfg.FOLLOW_BACK_CHECK_DAYS, account_id=account_id)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE account_id = ? AND status = 'following' AND followed_at <= ?",
            (account_id, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def get_users_to_unfollow(account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    days = get_config_int("unfollow_after_days", cfg.UNFOLLOW_AFTER_DAYS, account_id=account_id)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE account_id = ? AND status = 'following' AND followed_at <= ?",
            (account_id, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def get_users_followed_back(account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE account_id = ? AND status = 'followed_back'",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_requests(account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE account_id = ? AND status = 'pending_request'",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_expired_pending_requests(account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    days = get_config_int("pending_request_timeout_days", cfg.PENDING_REQUEST_TIMEOUT_DAYS, account_id=account_id)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM followed_users WHERE account_id = ? AND status = 'pending_request' AND followed_at <= ?",
            (account_id, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stats / counts (per account)
# ---------------------------------------------------------------------------

def count_today_actions(action: str, account_id: int = 0, db_path: Path | None = None) -> int:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE account_id = ? AND action = ? AND created_at >= ?",
            (account_id, action, today_start),
        ).fetchone()
        return row["cnt"] if row else 0


def count_hour_actions(action: str, account_id: int = 0, db_path: Path | None = None) -> int:
    hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE account_id = ? AND action = ? AND created_at >= ?",
            (account_id, action, hour_ago),
        ).fetchone()
        return row["cnt"] if row else 0


def get_stats(account_id: int = 0, db_path: Path | None = None) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        def _count(where: str) -> int:
            r = conn.execute(
                f"SELECT COUNT(*) as cnt FROM followed_users WHERE account_id = ? AND {where}",
                (account_id,),
            ).fetchone()
            return r["cnt"] if r else 0

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        total = _count("1=1")
        following = _count("status = 'following'")
        backed = _count("status = 'followed_back'")
        unfollowed = _count("status = 'unfollowed'")
        pending = _count("status = 'pending_request'")
        withdrawn = _count("status = 'request_withdrawn'")

        today_f = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE account_id = ? AND action = 'follow' AND created_at >= ?",
            (account_id, today_start),
        ).fetchone()["cnt"]
        today_u = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE account_id = ? AND action = 'unfollow' AND created_at >= ?",
            (account_id, today_start),
        ).fetchone()["cnt"]
        today_l = conn.execute(
            "SELECT COUNT(*) as cnt FROM activity_log WHERE account_id = ? AND action = 'like' AND created_at >= ?",
            (account_id, today_start),
        ).fetchone()["cnt"]

        return {
            "total_followed": total,
            "active_following": following,
            "followed_back": backed,
            "unfollowed": unfollowed,
            "pending_requests": pending,
            "request_withdrawn": withdrawn,
            "today_followed": today_f,
            "today_unfollowed": today_u,
            "today_liked": today_l,
            "followback_ratio": round(backed / max(total, 1) * 100, 1),
        }


def get_daily_stats(account_id: int = 0, days: int = 14, db_path: Path | None = None) -> list[dict]:
    """Get aggregated stats per day for charting."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()[:10]
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT DATE(created_at) as day, action, COUNT(*) as cnt
               FROM activity_log
               WHERE account_id = ? AND DATE(created_at) >= ?
               GROUP BY DATE(created_at), action
               ORDER BY day""",
            (account_id, cutoff),
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for r in rows:
            d = r["day"]
            if d not in result:
                result[d] = {"day": d, "follow": 0, "unfollow": 0, "like": 0, "story_view": 0}
            if r["action"] in result[d]:
                result[d][r["action"]] = r["cnt"]
        return list(result.values())


# ---------------------------------------------------------------------------
# Activity log (per account)
# ---------------------------------------------------------------------------

def log_activity(action: str, target_username: str = "", details: str = "",
                 account_id: int = 0, db_path: Path | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO activity_log (account_id, action, target_username, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, action, target_username, details, _now()),
        )


def get_recent_activity(limit: int = 50, account_id: int = 0, db_path: Path | None = None) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_global_recent_activity(limit: int = 50) -> list[dict]:
    """Activity across all accounts, with username joined."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT al.*, a.ig_username as account_username
               FROM activity_log al
               LEFT JOIN accounts a ON al.account_id = a.id
               ORDER BY al.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
