"""
Bot Engine – orchestrates all high-level flows:
  1. follow_followers_of(target, count)  – follow followers of a target account
  2. check_follow_backs()                – check who followed back
  3. unfollow_non_followers()            – unfollow people who didn't follow back
  4. unfollow_followed_back()            – unfollow people who DID follow back
  5. manage_pending_requests()           – handle private-account requests
  6. run_all()                           – automated cycle of all tasks

Every action passes through the Humanizer for delays and limit checks,
and through the Notifier for action-block / challenge alerts.
"""

from __future__ import annotations

import logging
import random
from typing import Callable

from bot.instagram import (
    ActionBlockError,
    ChallengeError,
    InstagramClient,
)
from bot import database as db
from bot import humanizer as hz
from bot.humanizer import (
    DriftingProbability,
    SessionBreakTracker,
    action_delay,
    pre_action_gate,
)
from bot.filters import FilterSettings, passes_filters
from bot import notifier
import config as cfg

logger = logging.getLogger("bot.engine")


class BotEngine:
    """Central orchestrator that ties together all bot components."""

    def __init__(self, client: InstagramClient):
        self.ig = client

        self._like_drift = DriftingProbability(
            lo=cfg.LIKE_CHANCE_MIN,
            hi=cfg.LIKE_CHANCE_MAX,
            period_min=cfg.DRIFT_PERIOD_MIN,
            period_max=cfg.DRIFT_PERIOD_MAX,
        )
        self._story_drift = DriftingProbability(
            lo=cfg.STORY_VIEW_CHANCE_MIN,
            hi=cfg.STORY_VIEW_CHANCE_MAX,
            period_min=cfg.DRIFT_PERIOD_MIN,
            period_max=cfg.DRIFT_PERIOD_MAX,
        )
        self._session_tracker = SessionBreakTracker()
        self._stopped = False

    # ------------------------------------------------------------------
    # Internal: stop flag (set on action block / challenge)
    # ------------------------------------------------------------------

    def _halt(self, reason: str) -> None:
        self._stopped = True
        logger.critical("Bot halted: %s", reason)

    def _check_stopped(self) -> bool:
        if self._stopped:
            notifier.notify_warning("Bot is stopped. Restart to continue.")
        return self._stopped

    # ------------------------------------------------------------------
    # 1. Follow followers of a target account
    # ------------------------------------------------------------------

    def follow_followers_of(self, target_username: str, count: int = 100) -> int:
        """
        Fetch followers of *target_username* and follow up to *count* of them.
        Returns the number of users actually followed.
        """
        if self._check_stopped():
            return 0

        notifier.notify_info(f"Fetching followers of @{target_username}...")

        try:
            target_id = self.ig.get_user_id(target_username)
        except ActionBlockError as exc:
            self._handle_action_block(str(exc))
            return 0
        except Exception as exc:
            notifier.notify_error(f"Could not find @{target_username}: {exc}")
            return 0

        try:
            followers = self.ig.get_followers(target_id, amount=count * 3)
        except ActionBlockError as exc:
            self._handle_action_block(str(exc))
            return 0

        notifier.notify_info(f"Fetched {len(followers)} followers of @{target_username}")

        filter_settings = FilterSettings.from_config()
        followed_count = 0

        follower_list = list(followers.items())
        random.shuffle(follower_list)

        for user_id_str, user_short in follower_list:
            if self._check_stopped():
                break
            if followed_count >= count:
                break

            if not pre_action_gate("follow"):
                notifier.notify_info("Follow limit reached, stopping for now.")
                break

            if db.user_exists(user_id_str):
                logger.debug("Skipping @%s (already in database)", user_short.username)
                continue

            # Fetch full user info for filtering
            try:
                user_info = self.ig.get_user_info(user_id_str)
            except ActionBlockError as exc:
                self._handle_action_block(str(exc))
                break
            except Exception as exc:
                logger.warning("Could not get info for %s: %s", user_id_str, exc)
                continue

            if not passes_filters(user_info, filter_settings):
                continue

            # --- Optional: like photos (drifting probability) ---
            like_before = self._like_drift.should_act() and random.random() < 0.5
            if like_before:
                self._do_likes(user_id_str, user_short.username)

            # --- Optional: view story (drifting probability) ---
            if self._story_drift.should_act():
                self._do_story_view(user_id_str, user_short.username)

            # --- Follow ---
            try:
                status = "pending_request" if user_info.is_private else "following"
                self.ig.follow_user(user_id_str)
                db.add_followed_user(
                    instagram_user_id=user_id_str,
                    username=user_short.username,
                    source_account=target_username,
                    is_private=user_info.is_private,
                    full_name=user_info.full_name,
                    status=status,
                )
                db.log_activity("follow", user_short.username, f"source={target_username}, private={user_info.is_private}")
                followed_count += 1
                notifier.notify_success(
                    f"[{followed_count}/{count}] Followed @{user_short.username}"
                    + (" (request)" if user_info.is_private else "")
                )
            except ActionBlockError as exc:
                self._handle_action_block(str(exc))
                break
            except Exception as exc:
                logger.warning("Failed to follow @%s: %s", user_short.username, exc)
                db.log_activity("error", user_short.username, f"follow failed: {exc}")
                continue

            # --- Optional: like photos AFTER follow ---
            if not like_before and self._like_drift.should_act():
                self._do_likes(user_id_str, user_short.username)

            self._session_tracker.tick()
            action_delay("follow")

        notifier.notify_info(f"Follow session done: {followed_count} users followed from @{target_username}")
        return followed_count

    # ------------------------------------------------------------------
    # 2. Check follow-backs
    # ------------------------------------------------------------------

    def check_follow_backs(self) -> tuple[int, int]:
        """
        Check users with status 'following' who are past the check threshold.
        Returns (checked_count, followback_count).
        """
        if self._check_stopped():
            return 0, 0

        users = db.get_users_to_check_followback()
        if not users:
            notifier.notify_info("No users due for follow-back check.")
            return 0, 0

        notifier.notify_info(f"Checking follow-backs for {len(users)} users...")
        checked = 0
        follow_backs = 0

        for user in users:
            if self._check_stopped():
                break
            if not pre_action_gate("check"):
                break

            try:
                status = self.ig.check_friendship_status(user["instagram_user_id"])
            except ActionBlockError as exc:
                self._handle_action_block(str(exc))
                break
            except Exception as exc:
                logger.warning("Check failed for @%s: %s", user["username"], exc)
                continue

            checked += 1
            if status["followed_by"]:
                db.update_user_status(user["instagram_user_id"], "followed_back")
                db.log_activity("check_followback", user["username"], "followed_back=True")
                follow_backs += 1
                notifier.notify_success(f"@{user['username']} followed back!")
            else:
                db.log_activity("check_followback", user["username"], "followed_back=False")
                logger.info("@%s has NOT followed back", user["username"])

            action_delay("check")

        notifier.notify_info(
            f"Follow-back check done: {checked} checked, {follow_backs} follow-backs detected"
        )
        return checked, follow_backs

    # ------------------------------------------------------------------
    # 3. Unfollow non-followers (past the deadline)
    # ------------------------------------------------------------------

    def unfollow_non_followers(self) -> int:
        """Unfollow users who didn't follow back within UNFOLLOW_AFTER_DAYS."""
        if self._check_stopped():
            return 0

        users = db.get_users_to_unfollow()
        if not users:
            notifier.notify_info("No non-followers to unfollow right now.")
            return 0

        notifier.notify_info(f"Unfollowing {len(users)} non-followers...")
        return self._do_unfollow_batch(users, reason="non-follower timeout")

    # ------------------------------------------------------------------
    # 4. Unfollow users who followed back
    # ------------------------------------------------------------------

    def unfollow_followed_back(self) -> int:
        """Unfollow users who already followed us back (grow net followers)."""
        if self._check_stopped():
            return 0

        users = db.get_users_followed_back()
        if not users:
            notifier.notify_info("No followed-back users to unfollow.")
            return 0

        notifier.notify_info(f"Unfollowing {len(users)} users who followed back...")
        return self._do_unfollow_batch(users, reason="followed back")

    def _do_unfollow_batch(self, users: list[dict], reason: str) -> int:
        unfollowed = 0
        for user in users:
            if self._check_stopped():
                break
            if not pre_action_gate("unfollow"):
                notifier.notify_info("Unfollow limit reached, stopping for now.")
                break

            try:
                self.ig.unfollow_user(user["instagram_user_id"])
                db.update_user_status(user["instagram_user_id"], "unfollowed")
                db.log_activity("unfollow", user["username"], reason)
                unfollowed += 1
                notifier.notify_success(f"Unfollowed @{user['username']} ({reason})")
            except ActionBlockError as exc:
                self._handle_action_block(str(exc))
                break
            except Exception as exc:
                logger.warning("Unfollow failed for @%s: %s", user["username"], exc)
                db.log_activity("error", user["username"], f"unfollow failed: {exc}")

            self._session_tracker.tick()
            action_delay("unfollow")

        notifier.notify_info(f"Unfollow batch done: {unfollowed} unfollowed ({reason})")
        return unfollowed

    # ------------------------------------------------------------------
    # 5. Manage pending follow requests (private accounts)
    # ------------------------------------------------------------------

    def manage_pending_requests(self) -> dict[str, int]:
        """
        For pending follow requests:
          - Check if accepted -> move to 'following'
          - Withdraw if expired -> move to 'request_withdrawn'
        Returns dict with counts.
        """
        if self._check_stopped():
            return {"accepted": 0, "withdrawn": 0, "still_pending": 0}

        pending = db.get_pending_requests()
        expired = db.get_expired_pending_requests()
        expired_ids = {u["instagram_user_id"] for u in expired}

        accepted = 0
        withdrawn = 0
        still_pending = 0

        if not pending:
            notifier.notify_info("No pending follow requests.")
            return {"accepted": 0, "withdrawn": 0, "still_pending": 0}

        notifier.notify_info(f"Managing {len(pending)} pending follow requests...")

        for user in pending:
            if self._check_stopped():
                break

            uid = user["instagram_user_id"]

            # Check if request was accepted
            try:
                status = self.ig.check_friendship_status(uid)
            except ActionBlockError as exc:
                self._handle_action_block(str(exc))
                break
            except Exception as exc:
                logger.warning("Status check failed for @%s: %s", user["username"], exc)
                still_pending += 1
                continue

            if status["following"] and not status["outgoing_request"]:
                db.update_user_status(uid, "following")
                db.log_activity("check_followback", user["username"], "private request accepted")
                accepted += 1
                notifier.notify_success(f"@{user['username']} accepted follow request")
            elif uid in expired_ids:
                # Withdraw expired request
                try:
                    self.ig.withdraw_follow_request(uid)
                    db.update_user_status(uid, "request_withdrawn")
                    db.log_activity("withdraw_request", user["username"], "request expired")
                    withdrawn += 1
                    notifier.notify_info(f"Withdrew request for @{user['username']} (expired)")
                except ActionBlockError as exc:
                    self._handle_action_block(str(exc))
                    break
                except Exception as exc:
                    logger.warning("Withdraw failed for @%s: %s", user["username"], exc)
                    still_pending += 1
            else:
                still_pending += 1

            action_delay("check")

        result = {"accepted": accepted, "withdrawn": withdrawn, "still_pending": still_pending}
        notifier.notify_info(f"Request management done: {result}")
        return result

    # ------------------------------------------------------------------
    # 6. Run all tasks in sequence
    # ------------------------------------------------------------------

    def run_all(self, target_username: str | None = None, follow_count: int = 100) -> None:
        """Execute a full cycle: follow -> check -> unfollow -> requests."""
        if self._check_stopped():
            return

        notifier.notify_info("Starting full bot cycle...")

        # Step 1: Follow new users (if target provided)
        if target_username:
            self.follow_followers_of(target_username, follow_count)

        if self._check_stopped():
            return

        # Step 2: Check follow-backs
        self.check_follow_backs()

        if self._check_stopped():
            return

        # Step 3: Unfollow users who followed back
        self.unfollow_followed_back()

        if self._check_stopped():
            return

        # Step 4: Unfollow non-followers past deadline
        self.unfollow_non_followers()

        if self._check_stopped():
            return

        # Step 5: Manage private account requests
        self.manage_pending_requests()

        notifier.notify_info("Full bot cycle completed.")

    # ------------------------------------------------------------------
    # Helpers: likes, stories, error handling
    # ------------------------------------------------------------------

    def _do_likes(self, user_id: str, username: str) -> None:
        """Like 1-N recent posts for a user."""
        try:
            medias = self.ig.get_user_medias(user_id, amount=cfg.LIKE_COUNT_MAX)
            if not medias:
                return
            num_to_like = random.randint(cfg.LIKE_COUNT_MIN, min(cfg.LIKE_COUNT_MAX, len(medias)))
            for media in medias[:num_to_like]:
                self.ig.like_media(str(media.pk))
                db.increment_likes(user_id)
                db.log_activity("like", username, f"media_id={media.pk}")
                action_delay("like")
        except ActionBlockError:
            raise
        except Exception as exc:
            logger.warning("Like failed for @%s: %s", username, exc)

    def _do_story_view(self, user_id: str, username: str) -> None:
        """View available stories for a user."""
        try:
            stories = self.ig.get_user_stories(user_id)
            if not stories:
                return
            story = stories[0]
            self.ig.view_story(str(story.pk))
            db.increment_stories(user_id)
            db.log_activity("story_view", username, f"story_id={story.pk}")
            action_delay("story_view")
        except ActionBlockError:
            raise
        except Exception as exc:
            logger.warning("Story view failed for @%s: %s", username, exc)

    def _handle_action_block(self, details: str) -> None:
        """Handle an action block: log, notify, halt."""
        db.log_activity("action_block", "", details)
        notifier.notify_action_block(details)
        self._halt(f"Action block: {details}")
