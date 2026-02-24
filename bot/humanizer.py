"""
Humanizer – makes the bot behave like a real person.

Key features:
  - Gaussian (not uniform) random delays per action type
  - Drifting probability system: like/story chances shift periodically
    within a configurable range so no fixed pattern emerges
  - Session breaks after N actions (N itself varies)
  - Daily limit enforcement with +/- variance
  - Hourly limit enforcement
  - Active-hours window
  - Optional warm-up mode (gradual ramp-up over weeks)

Every manager (Follow, Unfollow, Check, Request) calls the Humanizer
before each action.
"""

from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime
from typing import Literal

from bot import database as db
import config as cfg

logger = logging.getLogger("bot.humanizer")

ActionType = Literal["follow", "unfollow", "like", "story_view", "check", "fetch"]

# ---------------------------------------------------------------------------
# Gaussian-bounded delay
# ---------------------------------------------------------------------------

def gaussian_delay(low: float, high: float) -> float:
    """
    Sample from a truncated Gaussian distribution between *low* and *high*.
    Mean = midpoint, stddev = (high-low)/4 so ~95% of samples fall in range.
    Values are clamped to [low, high].
    """
    mean = (low + high) / 2
    stddev = (high - low) / 4
    value = random.gauss(mean, stddev)
    return max(low, min(high, value))


def sleep_human(low: float, high: float, label: str = "") -> None:
    """Sleep for a Gaussian-distributed duration; log what we're waiting for."""
    seconds = gaussian_delay(low, high)
    if label:
        logger.info("Waiting %.1fs (%s)", seconds, label)
    else:
        logger.info("Waiting %.1fs", seconds)
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Delay configs per action type
# ---------------------------------------------------------------------------

_DELAY_MAP: dict[ActionType, tuple[str, str, float, float]] = {
    #  action       cfg_min_attr            cfg_max_attr            fallback_min  fallback_max
    "follow":      ("FOLLOW_DELAY_MIN",     "FOLLOW_DELAY_MAX",     40.0, 180.0),
    "unfollow":    ("UNFOLLOW_DELAY_MIN",   "UNFOLLOW_DELAY_MAX",   35.0, 120.0),
    "like":        ("LIKE_DELAY_MIN",       "LIKE_DELAY_MAX",       8.0,  25.0),
    "story_view":  ("STORY_VIEW_DELAY_MIN", "STORY_VIEW_DELAY_MAX", 5.0,  18.0),
    "check":       ("CHECK_DELAY_MIN",      "CHECK_DELAY_MAX",      6.0,  15.0),
    "fetch":       ("FETCH_DELAY_MIN",      "FETCH_DELAY_MAX",      10.0,  30.0),
}


def action_delay(action: ActionType) -> None:
    """Sleep for a human-like delay appropriate for the given action type."""
    cfg_min_attr, cfg_max_attr, fb_min, fb_max = _DELAY_MAP[action]
    low = getattr(cfg, cfg_min_attr, fb_min)
    high = getattr(cfg, cfg_max_attr, fb_max)
    sleep_human(low, high, label=f"{action} delay")


# ---------------------------------------------------------------------------
# Drifting Probability
# ---------------------------------------------------------------------------

class DriftingProbability:
    """
    Probability that shifts to a new random value within [lo, hi] every
    N actions, where N itself is random within [period_min, period_max].
    This prevents fixed patterns like "like every 3rd person".
    """

    def __init__(self, lo: float, hi: float, period_min: int, period_max: int):
        self.lo = lo
        self.hi = hi
        self.period_min = period_min
        self.period_max = period_max
        self._current_prob: float = random.uniform(lo, hi)
        self._actions_until_drift: int = random.randint(period_min, period_max)
        self._action_count: int = 0

    def should_act(self) -> bool:
        """Roll the dice with the current drifting probability."""
        self._action_count += 1
        if self._action_count >= self._actions_until_drift:
            self._reroll()
        return random.random() < self._current_prob

    def _reroll(self) -> None:
        old = self._current_prob
        self._current_prob = random.uniform(self.lo, self.hi)
        self._actions_until_drift = random.randint(self.period_min, self.period_max)
        self._action_count = 0
        logger.debug(
            "Drift: probability %.2f -> %.2f, next drift in %d actions",
            old, self._current_prob, self._actions_until_drift,
        )

    @property
    def current(self) -> float:
        return self._current_prob


# ---------------------------------------------------------------------------
# Session break tracker
# ---------------------------------------------------------------------------

class SessionBreakTracker:
    """
    Triggers a long pause after a random number of actions.
    The threshold re-randomises after each break.
    """

    def __init__(
        self,
        break_after_min: int | None = None,
        break_after_max: int | None = None,
        break_duration_min: float | None = None,
        break_duration_max: float | None = None,
    ):
        self.break_after_min = break_after_min or cfg.SESSION_BREAK_AFTER_MIN
        self.break_after_max = break_after_max or cfg.SESSION_BREAK_AFTER_MAX
        self.break_duration_min = break_duration_min or cfg.SESSION_BREAK_DURATION_MIN
        self.break_duration_max = break_duration_max or cfg.SESSION_BREAK_DURATION_MAX
        self._threshold = random.randint(self.break_after_min, self.break_after_max)
        self._action_count = 0

    def tick(self) -> None:
        """Call after every action. Sleeps if break threshold is reached."""
        self._action_count += 1
        if self._action_count >= self._threshold:
            duration = gaussian_delay(self.break_duration_min, self.break_duration_max)
            logger.info(
                "Session break: %d actions done, pausing %.0fs (%.1f min)",
                self._action_count, duration, duration / 60,
            )
            time.sleep(duration)
            self._action_count = 0
            self._threshold = random.randint(self.break_after_min, self.break_after_max)
            logger.info("Resuming. Next break after ~%d actions", self._threshold)


# ---------------------------------------------------------------------------
# Limit checkers
# ---------------------------------------------------------------------------

def check_daily_limit(action: str, limit_key: str, default_limit: int, account_id: int = 0) -> bool:
    """Return True if we are still under the daily limit (with variance)."""
    base_limit = db.get_config_int(limit_key, default_limit, account_id=account_id)
    variance = cfg.DAILY_LIMIT_VARIANCE
    effective = int(base_limit * random.uniform(1 - variance, 1 + variance))
    today_count = db.count_today_actions(action, account_id=account_id)
    under = today_count < effective
    if not under:
        logger.info("Daily %s limit reached: %d/%d", action, today_count, effective)
    return under


def check_hourly_limit(action: str, limit_key: str, default_limit: int, account_id: int = 0) -> bool:
    """Return True if we are still under the hourly limit."""
    limit = db.get_config_int(limit_key, default_limit, account_id=account_id)
    hour_count = db.count_hour_actions(action, account_id=account_id)
    under = hour_count < limit
    if not under:
        logger.info("Hourly %s limit reached: %d/%d", action, hour_count, limit)
    return under


def can_follow(account_id: int = 0) -> bool:
    return (
        check_daily_limit("follow", "daily_follow_limit", cfg.DAILY_FOLLOW_LIMIT, account_id)
        and check_hourly_limit("follow", "hourly_follow_limit", cfg.HOURLY_FOLLOW_LIMIT, account_id)
    )


def can_unfollow(account_id: int = 0) -> bool:
    return (
        check_daily_limit("unfollow", "daily_unfollow_limit", cfg.DAILY_UNFOLLOW_LIMIT, account_id)
        and check_hourly_limit("unfollow", "hourly_unfollow_limit", cfg.HOURLY_UNFOLLOW_LIMIT, account_id)
    )


# ---------------------------------------------------------------------------
# Active hours
# ---------------------------------------------------------------------------

def is_active_hours() -> bool:
    """Check if the current hour falls within the configured active window."""
    start = db.get_config_int("active_hours_start", cfg.ACTIVE_HOURS_START)
    end = db.get_config_int("active_hours_end", cfg.ACTIVE_HOURS_END)
    now_hour = datetime.now().hour
    if start <= end:
        return start <= now_hour < end
    # Wrapping window (e.g. 22 -> 6)
    return now_hour >= start or now_hour < end


def wait_for_active_hours() -> None:
    """Block until we enter the active-hours window."""
    while not is_active_hours():
        logger.info("Outside active hours, sleeping 5 minutes...")
        time.sleep(300)


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------

_WARMUP_SCHEDULE = {
    1: 15,   # week 1: max 15 follows/day
    2: 40,   # week 2: max 40
    3: 70,   # week 3: max 70
}


def get_warmup_daily_limit(start_date: datetime | None = None, account_id: int = 0) -> int | None:
    """
    If warm-up is enabled, return the reduced daily follow limit for the
    current week. Returns None if warm-up is disabled or past week 3.
    """
    if not db.get_config_bool("warmup_enabled", cfg.WARMUP_ENABLED, account_id=account_id):
        return None

    start_str = db.get_config("warmup_start_date", account_id=account_id)
    if not start_str:
        now_str = datetime.utcnow().isoformat()
        db.set_config("warmup_start_date", now_str, "Date warm-up mode started", account_id=account_id)
        start_str = now_str

    start = datetime.fromisoformat(start_str)
    days_elapsed = (datetime.utcnow() - start).days
    week = days_elapsed // 7 + 1

    return _WARMUP_SCHEDULE.get(week)


# ---------------------------------------------------------------------------
# Convenience: full pre-action gate
# ---------------------------------------------------------------------------

def pre_action_gate(action: ActionType, account_id: int = 0) -> bool:
    """
    Combined gate: checks active hours, daily limit, hourly limit.
    Returns True if the action is allowed right now.
    """
    wait_for_active_hours()

    warmup_limit = get_warmup_daily_limit(account_id=account_id)
    if warmup_limit is not None and action == "follow":
        today = db.count_today_actions("follow", account_id=account_id)
        if today >= warmup_limit:
            logger.info("Warm-up daily limit reached: %d/%d", today, warmup_limit)
            return False

    if action == "follow":
        return can_follow(account_id)
    elif action == "unfollow":
        return can_unfollow(account_id)
    return True
