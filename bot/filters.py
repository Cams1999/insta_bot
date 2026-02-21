"""
Configurable filter engine for deciding which followers to follow.
Each filter is a callable predicate; the engine runs all enabled filters
and returns True only if every predicate passes.

Filters are loaded from bot_config (database) so they can be changed at
runtime through the CLI without restarting the bot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from instagrapi.types import User

from bot import database as db
import config as cfg

logger = logging.getLogger("bot.filters")


@dataclass
class FilterSettings:
    """Snapshot of all filter settings, loaded once per batch from DB."""

    has_profile_pic: bool = True
    is_private_allowed: bool = True
    min_posts: int = 1
    max_posts: int | None = None
    min_followers: int = 0
    max_followers: int = 10_000
    min_following: int = 0
    max_following: int | None = None

    @classmethod
    def from_config(cls) -> FilterSettings:
        return cls(
            has_profile_pic=db.get_config_bool("filter_has_profile_pic", cfg.FILTER_HAS_PROFILE_PIC),
            is_private_allowed=db.get_config_bool("filter_is_private_allowed", cfg.FILTER_IS_PRIVATE_ALLOWED),
            min_posts=db.get_config_int("filter_min_posts", cfg.FILTER_MIN_POSTS),
            max_posts=_int_or_none(db.get_config("filter_max_posts")),
            min_followers=db.get_config_int("filter_min_followers", cfg.FILTER_MIN_FOLLOWERS),
            max_followers=_int_or_none(db.get_config("filter_max_followers", str(cfg.FILTER_MAX_FOLLOWERS))),
            min_following=db.get_config_int("filter_min_following", cfg.FILTER_MIN_FOLLOWING),
            max_following=_int_or_none(db.get_config("filter_max_following")),
        )


def _int_or_none(val: str | None) -> int | None:
    if val is None or val.lower() in ("none", "null", ""):
        return None
    return int(val)


def passes_filters(user_info: User, settings: FilterSettings | None = None) -> bool:
    """
    Run all filter predicates on user_info.
    Returns True if the user passes every enabled filter.
    """
    s = settings or FilterSettings.from_config()
    reasons: list[str] = []

    if s.has_profile_pic and not user_info.profile_pic_url:
        reasons.append("no profile pic")

    if not s.is_private_allowed and user_info.is_private:
        reasons.append("private account")

    if user_info.media_count < s.min_posts:
        reasons.append(f"posts {user_info.media_count} < min {s.min_posts}")

    if s.max_posts is not None and user_info.media_count > s.max_posts:
        reasons.append(f"posts {user_info.media_count} > max {s.max_posts}")

    if user_info.follower_count < s.min_followers:
        reasons.append(f"followers {user_info.follower_count} < min {s.min_followers}")

    if s.max_followers is not None and user_info.follower_count > s.max_followers:
        reasons.append(f"followers {user_info.follower_count} > max {s.max_followers}")

    if user_info.following_count < s.min_following:
        reasons.append(f"following {user_info.following_count} < min {s.min_following}")

    if s.max_following is not None and user_info.following_count > s.max_following:
        reasons.append(f"following {user_info.following_count} > max {s.max_following}")

    if reasons:
        logger.debug("Filtered out @%s: %s", user_info.username, "; ".join(reasons))
        return False

    return True
