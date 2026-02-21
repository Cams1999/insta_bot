"""
Notification system.
Phase 1: terminal output via rich.
Phase 2 (later): Telegram bot notifications.
Phase 3 (later): Email notifications.
"""

from __future__ import annotations

import logging
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger("bot.notifier")
console = Console()


def notify_action_block(details: str = "") -> None:
    console.print(Panel(
        f"[bold red]ACTION BLOCK DETECTED[/bold red]\n\n"
        f"Instagram has temporarily blocked actions on this account.\n"
        f"The bot has stopped automatically.\n\n"
        f"Details: {details or 'N/A'}\n\n"
        f"[yellow]Wait at least 24-48 hours before restarting.[/yellow]",
        title="Warning",
        border_style="red",
    ))
    logger.error("ACTION BLOCK: %s", details)


def notify_challenge(details: str = "") -> None:
    console.print(Panel(
        f"[bold red]CHALLENGE / VERIFICATION REQUIRED[/bold red]\n\n"
        f"Instagram requires email or SMS verification.\n"
        f"Log in manually to resolve the challenge, then restart the bot.\n\n"
        f"Details: {details or 'N/A'}",
        title="Warning",
        border_style="red",
    ))
    logger.error("CHALLENGE REQUIRED: %s", details)


def notify_info(message: str) -> None:
    console.print(f"[cyan]INFO:[/cyan] {message}")
    logger.info(message)


def notify_success(message: str) -> None:
    console.print(f"[green]OK:[/green] {message}")
    logger.info(message)


def notify_warning(message: str) -> None:
    console.print(f"[yellow]WARN:[/yellow] {message}")
    logger.warning(message)


def notify_error(message: str) -> None:
    console.print(f"[red]ERROR:[/red] {message}")
    logger.error(message)
