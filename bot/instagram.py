"""
Instagrapi wrapper.
Handles login (with session caching & device UUID persistence),
optional 2FA, proxy support, and all Instagram interactions:
  follow, unfollow, like, story_view, get_followers,
  check_follow_back, withdraw_request.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    UserNotFound,
)
from instagrapi.mixins.challenge import ChallengeChoice
from instagrapi.types import User, UserShort

import config as cfg

logger = logging.getLogger("bot.instagram")

# Maps instagrapi exception types to friendly action-block categories
ACTION_BLOCK_EXCEPTIONS = (
    FeedbackRequired,
    PleaseWaitFewMinutes,
)


class ActionBlockError(Exception):
    """Raised when Instagram returns an action block / rate-limit."""


class ChallengeError(Exception):
    """Raised when Instagram requires a challenge (email/SMS verification)."""


class InstagramClient:
    """Thin wrapper around instagrapi.Client with session persistence."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        session_dir: Path | None = None,
        proxy_url: str | None = None,
        totp_seed: str | None = None,
    ):
        self.username = username or cfg.IG_USERNAME
        self.password = password or cfg.IG_PASSWORD
        self.totp_seed = totp_seed or cfg.IG_2FA_SEED
        self.proxy_url = proxy_url or cfg.PROXY_URL
        self.session_dir = session_dir or cfg.SESSION_DIR
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._session_path = self.session_dir / f"{self.username}_session.json"
        self.cl = Client()
        self.cl.delay_range = [1, 3]
        self.cl.challenge_code_handler = self._challenge_code_handler
        self._set_device()
        self._logged_in = False

    def _set_device(self) -> None:
        """Use a common device fingerprint to reduce suspicion."""
        self.cl.set_device({
            "app_version": "269.0.0.18.75",
            "android_version": 31,
            "android_release": "12.0.0",
            "dpi": "420dpi",
            "resolution": "1080x2400",
            "manufacturer": "samsung",
            "device": "o1s",
            "model": "SM-G991B",
            "cpu": "exynos2100",
            "version_code": "314665256",
        })

    def _challenge_code_handler(self, username: str, choice: ChallengeChoice) -> str | bool:
        """Prompt user to enter verification code from SMS or email."""
        choice_str = "SMS" if choice == ChallengeChoice.SMS else "EMAIL"
        logger.info("Instagram asks for %s verification code for @%s", choice_str, username)
        try:
            code = input(f"Enter 6-digit code from {choice_str}: ").strip()
            if code and code.isdigit() and len(code) == 6:
                return code
        except (EOFError, KeyboardInterrupt):
            pass
        return False

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self) -> None:
        if self.proxy_url:
            self.cl.set_proxy(self.proxy_url)
            logger.info("Proxy set: %s", self.proxy_url.split("@")[-1] if "@" in self.proxy_url else "configured")

        self.cl.set_locale(cfg.IG_LOCALE)
        self.cl.set_country(cfg.IG_COUNTRY)
        self.cl.set_country_code(cfg.IG_COUNTRY_CODE)
        self.cl.set_timezone_offset(cfg.IG_TIMEZONE_OFFSET)

        login_via_session = self._try_session_login()
        if not login_via_session:
            self._try_password_login()

        self._logged_in = True
        logger.info("Logged in as %s (user_id=%s)", self.username, self.cl.user_id)

    def _try_session_login(self) -> bool:
        if not self._session_path.exists():
            return False
        try:
            session = self.cl.load_settings(self._session_path)
            if not session:
                return False
            self.cl.set_settings(session)
            self.cl.login(self.username, self.password)
            self.cl.get_timeline_feed()
            logger.info("Session login successful")
            return True
        except LoginRequired:
            logger.info("Session expired, re-authenticating with same device UUIDs")
            old = self.cl.get_settings()
            self.cl.set_settings({})
            self.cl.set_uuids(old["uuids"])
            try:
                self._do_login()
                return True
            except Exception as exc:
                logger.warning("Re-auth failed: %s", exc)
                return False
        except Exception as exc:
            logger.warning("Session login failed: %s", exc)
            return False

    def _try_password_login(self) -> None:
        logger.info("Logging in with username/password")
        try:
            self._do_login()
        except ChallengeRequired as exc:
            raise ChallengeError(
                "Instagram requires verification (email/SMS). "
                "Please log in manually, complete the challenge, then try again."
            ) from exc
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(f"Action block during login: {exc}") from exc

    def _do_login(self) -> None:
        self._patch_login_flow()
        if self.totp_seed:
            from instagrapi.mixins.totp import TOTPMixin
            code = TOTPMixin.generate_totp_code(self.totp_seed)
            self.cl.login(self.username, self.password, verification_code=code)
        else:
            self.cl.login(self.username, self.password)
        self._save_session()

    def _patch_login_flow(self) -> None:
        """Make login_flow fault-tolerant so the bot survives reels_tray / challenge failures."""
        original_login_flow = self.cl.login_flow

        def resilient_login_flow() -> bool:
            self._save_session()
            logger.info("Session saved before post-login checks")
            try:
                return original_login_flow()
            except Exception as exc:
                logger.warning(
                    "Post-login check failed (non-fatal): %s — continuing with saved session",
                    exc,
                )
                return True

        self.cl.login_flow = resilient_login_flow

    def _save_session(self) -> None:
        self.cl.dump_settings(self._session_path)
        logger.debug("Session saved to %s", self._session_path)

    def logout(self) -> None:
        if self._logged_in:
            self._save_session()
            self._logged_in = False

    # ------------------------------------------------------------------
    # User info
    # ------------------------------------------------------------------

    def get_user_id(self, username: str) -> str:
        try:
            return str(self.cl.user_id_from_username(username))
        except UserNotFound:
            raise
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def get_user_info(self, user_id: str) -> User:
        try:
            return self.cl.user_info(int(user_id))
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def get_followers(self, user_id: str, amount: int = 0) -> dict[str, UserShort]:
        """Return dict {user_id_str: UserShort}. amount=0 means all."""
        try:
            result = self.cl.user_followers(int(user_id), amount=amount)
            return {str(k): v for k, v in result.items()}
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def get_my_following(self, amount: int = 0) -> dict[str, UserShort]:
        """People the logged-in account is following."""
        try:
            result = self.cl.user_following(self.cl.user_id, amount=amount)
            return {str(k): v for k, v in result.items()}
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Follow / Unfollow
    # ------------------------------------------------------------------

    def follow_user(self, user_id: str) -> bool:
        try:
            return self.cl.user_follow(int(user_id))
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def unfollow_user(self, user_id: str) -> bool:
        try:
            return self.cl.user_unfollow(int(user_id))
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Check follow-back
    # ------------------------------------------------------------------

    def check_is_following_me(self, user_id: str) -> bool:
        """Check if user_id follows the logged-in account."""
        try:
            info = self.cl.user_info(int(user_id))
            my_followers = self.cl.user_followers(self.cl.user_id, amount=0)
            return int(user_id) in my_followers
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def check_friendship_status(self, user_id: str) -> dict[str, bool]:
        """Return friendship status: following, followed_by, blocking, etc."""
        try:
            status = self.cl.user_friendship(int(user_id))
            return {
                "following": status.following,
                "followed_by": status.followed_by,
                "is_private": status.is_private,
                "outgoing_request": status.outgoing_request,
                "incoming_request": getattr(status, "incoming_request", False),
            }
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Like / Story
    # ------------------------------------------------------------------

    def get_user_medias(self, user_id: str, amount: int = 3) -> list:
        try:
            return self.cl.user_medias(int(user_id), amount=amount)
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def like_media(self, media_id: str) -> bool:
        try:
            return self.cl.media_like(media_id)
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def get_user_stories(self, user_id: str) -> list:
        try:
            return self.cl.user_stories(int(user_id))
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc

    def view_story(self, story_id: str) -> bool:
        """Mark a story as seen."""
        try:
            self.cl.story_seen([int(story_id)])
            return True
        except ACTION_BLOCK_EXCEPTIONS as exc:
            raise ActionBlockError(str(exc)) from exc
        except Exception as exc:
            logger.warning("Could not view story %s: %s", story_id, exc)
            return False

    # ------------------------------------------------------------------
    # Withdraw follow request (private accounts)
    # ------------------------------------------------------------------

    def withdraw_follow_request(self, user_id: str) -> bool:
        """Withdraw a pending follow request by unfollowing."""
        return self.unfollow_user(user_id)
