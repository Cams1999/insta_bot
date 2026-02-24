"""
Bot Manager – manages multiple BotEngine instances running in background threads.
Each Instagram account gets its own thread with its own BotEngine + InstagramClient.
Provides start/stop/pause control per account.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from bot.instagram import InstagramClient, ActionBlockError, ChallengeError
from bot.engine import BotEngine
from bot import database as db
import config as cfg

logger = logging.getLogger("bot.manager")


class AccountWorker:
    """Wraps a BotEngine running in a background thread for a single account."""

    def __init__(self, account_id: int, account_data: dict):
        self.account_id = account_id
        self.account_data = account_data
        self.engine: BotEngine | None = None
        self.client: InstagramClient | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._error

    def test_login(self) -> tuple[bool, str]:
        """Test if the credentials work. Returns (success, message)."""
        try:
            client = InstagramClient(
                username=self.account_data["ig_username"],
                password=self.account_data["ig_password"],
                totp_seed=self.account_data.get("ig_2fa_seed", ""),
                proxy_url=self.account_data.get("proxy_url", ""),
            )
            client.login()
            client.logout()
            return True, f"Login successful for @{self.account_data['ig_username']}"
        except ChallengeError as exc:
            return False, f"Challenge required: {exc}"
        except ActionBlockError as exc:
            return False, f"Action block: {exc}"
        except Exception as exc:
            return False, f"Login failed: {exc}"

    def start(self, target_username: str = "", follow_count: int = 100) -> tuple[bool, str]:
        """Start the bot in a background thread."""
        if self.is_running:
            return False, "Bot is already running"

        self._stop_event.clear()
        self._error = None

        try:
            self.client = InstagramClient(
                username=self.account_data["ig_username"],
                password=self.account_data["ig_password"],
                totp_seed=self.account_data.get("ig_2fa_seed", ""),
                proxy_url=self.account_data.get("proxy_url", ""),
            )
            self.client.login()
        except Exception as exc:
            self._error = str(exc)
            return False, f"Login failed: {exc}"

        self.engine = BotEngine(self.client, account_id=self.account_id)

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(target_username, follow_count),
            daemon=True,
            name=f"bot-{self.account_data['ig_username']}",
        )
        self._running = True
        self._thread.start()

        db.set_account_status(self.account_id, "running")
        db.update_account(self.account_id, target_username=target_username, follow_count=follow_count)
        logger.info("Started bot for @%s", self.account_data["ig_username"])
        return True, "Bot started"

    def stop(self) -> None:
        """Signal the bot to stop."""
        self._stop_event.set()
        self._running = False
        db.set_account_status(self.account_id, "stopped")
        if self.client:
            self.client.logout()
        logger.info("Stopped bot for @%s", self.account_data["ig_username"])

    def _run_loop(self, target_username: str, follow_count: int) -> None:
        """Main bot loop running in background thread."""
        try:
            self.engine.run_all(target_username=target_username or None, follow_count=follow_count)
        except Exception as exc:
            self._error = str(exc)
            logger.exception("Bot error for @%s: %s", self.account_data["ig_username"], exc)
            db.log_activity("error", "", f"Bot crashed: {exc}", account_id=self.account_id)
        finally:
            self._running = False
            db.set_account_status(self.account_id, "idle")
            if self.client:
                self.client.logout()


class BotManager:
    """Singleton manager that tracks all account workers."""

    def __init__(self):
        self._workers: dict[int, AccountWorker] = {}
        self._lock = threading.Lock()

    def get_worker(self, account_id: int) -> AccountWorker | None:
        return self._workers.get(account_id)

    def get_all_statuses(self) -> dict[int, dict[str, Any]]:
        result = {}
        for aid, w in self._workers.items():
            result[aid] = {
                "running": w.is_running,
                "error": w.last_error,
            }
        return result

    def start_bot(self, account_id: int, target_username: str = "",
                  follow_count: int = 100) -> tuple[bool, str]:
        account = db.get_account(account_id)
        if not account:
            return False, "Account not found"

        with self._lock:
            worker = self._workers.get(account_id)
            if worker and worker.is_running:
                return False, "Bot is already running for this account"

            worker = AccountWorker(account_id, account)
            self._workers[account_id] = worker

        return worker.start(target_username, follow_count)

    def stop_bot(self, account_id: int) -> tuple[bool, str]:
        worker = self._workers.get(account_id)
        if not worker:
            return False, "No bot instance found"
        if not worker.is_running:
            return False, "Bot is not running"
        worker.stop()
        return True, "Bot stopped"

    def test_account(self, account_id: int) -> tuple[bool, str]:
        account = db.get_account(account_id)
        if not account:
            return False, "Account not found"
        worker = AccountWorker(account_id, account)
        return worker.test_login()


bot_manager = BotManager()
