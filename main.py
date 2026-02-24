"""
Instagram Bot – CLI entry point.
Interactive menu built with rich for a clean terminal experience.
"""

from __future__ import annotations

import logging
import sys
import traceback

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.logging import RichHandler

from bot import database as db
from bot.instagram import InstagramClient, ActionBlockError, ChallengeError
from bot.engine import BotEngine
from bot import notifier
import config as cfg

console = Console()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    logging.getLogger("bot").setLevel(logging.INFO)
    logging.getLogger("instagrapi").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

def print_banner() -> None:
    console.print(Panel(
        "[bold cyan]Instagram Follow/Unfollow Bot v1.0[/bold cyan]\n"
        "Automated follower growth with human-like behavior",
        border_style="cyan",
    ))


def print_menu() -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan", width=4)
    table.add_column()
    table.add_row("1.", "Follow followers of an account")
    table.add_row("2.", "Check follow-backs")
    table.add_row("3.", "Unfollow non-followers (expired)")
    table.add_row("4.", "Unfollow users who followed back")
    table.add_row("5.", "Manage private follow-requests")
    table.add_row("6.", "View statistics")
    table.add_row("7.", "View / edit configuration")
    table.add_row("8.", "View recent activity")
    table.add_row("9.", "Run full cycle (all tasks)")
    table.add_row("0.", "Exit")
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def action_follow(engine: BotEngine) -> None:
    target = Prompt.ask("[cyan]Target username (without @)[/cyan]").strip().lstrip("@")
    if not target:
        notifier.notify_warning("No username entered.")
        return
    count = IntPrompt.ask("[cyan]How many users to follow?[/cyan]", default=100)
    engine.follow_followers_of(target, count)


def action_check_followbacks(engine: BotEngine) -> None:
    engine.check_follow_backs()


def action_unfollow_non_followers(engine: BotEngine) -> None:
    engine.unfollow_non_followers()


def action_unfollow_followed_back(engine: BotEngine) -> None:
    engine.unfollow_followed_back()


def action_manage_requests(engine: BotEngine) -> None:
    engine.manage_pending_requests()


def action_stats() -> None:
    stats = db.get_stats()
    table = Table(title="Bot Statistics", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total followed (all time)", str(stats["total_followed"]))
    table.add_row("Currently following", str(stats["active_following"]))
    table.add_row("Followed back", str(stats["followed_back"]))
    table.add_row("Unfollowed", str(stats["unfollowed"]))
    table.add_row("Pending requests", str(stats["pending_requests"]))
    table.add_row("Requests withdrawn", str(stats["request_withdrawn"]))
    table.add_row("─" * 30, "─" * 10)
    table.add_row("Today followed", str(stats["today_followed"]))
    table.add_row("Today unfollowed", str(stats["today_unfollowed"]))
    table.add_row("Follow-back ratio", f"{stats['followback_ratio']}%")

    console.print(table)


def action_config() -> None:
    configs = db.get_all_config()
    table = Table(title="Bot Configuration", border_style="cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Description", style="dim")

    for c in configs:
        table.add_row(c["key"], c["value"], c["description"])

    console.print(table)

    if Prompt.ask("\n[cyan]Edit a setting? (y/n)[/cyan]", default="n").lower() == "y":
        key = Prompt.ask("[cyan]Setting key[/cyan]").strip()
        matching = [c for c in configs if c["key"] == key]
        if not matching:
            notifier.notify_warning(f"Unknown key: {key}")
            return
        current = matching[0]["value"]
        console.print(f"  Current value: [yellow]{current}[/yellow]")
        new_val = Prompt.ask("[cyan]New value[/cyan]").strip()
        if new_val:
            db.set_config(key, new_val)
            notifier.notify_success(f"Updated {key} = {new_val}")


def action_recent_activity() -> None:
    activities = db.get_recent_activity(limit=30)
    if not activities:
        notifier.notify_info("No activity logged yet.")
        return

    table = Table(title="Recent Activity (last 30)", border_style="cyan")
    table.add_column("Time", style="dim", width=20)
    table.add_column("Action", style="bold")
    table.add_column("Target")
    table.add_column("Details", style="dim")

    for a in activities:
        action_style = {
            "follow": "green",
            "unfollow": "red",
            "like": "yellow",
            "story_view": "magenta",
            "error": "bold red",
            "action_block": "bold red",
        }.get(a["action"], "white")

        table.add_row(
            a["created_at"][:19],
            f"[{action_style}]{a['action']}[/{action_style}]",
            a["target_username"] or "",
            (a["details"] or "")[:60],
        )

    console.print(table)


def action_run_all(engine: BotEngine) -> None:
    target = Prompt.ask(
        "[cyan]Target username to follow from (leave empty to skip following)[/cyan]",
        default="",
    ).strip().lstrip("@")
    count = 100
    if target:
        count = IntPrompt.ask("[cyan]How many to follow?[/cyan]", default=100)
    engine.run_all(target_username=target or None, follow_count=count)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    print_banner()

    # Initialize database
    notifier.notify_info("Initializing database...")
    db.init_db()

    # Check credentials
    if not cfg.IG_USERNAME or not cfg.IG_PASSWORD:
        console.print(Panel(
            "[bold red]Missing Instagram credentials![/bold red]\n\n"
            "Create a [bold].env[/bold] file in the project root with:\n"
            "  IG_USERNAME=your_username\n"
            "  IG_PASSWORD=your_password\n\n"
            "See [bold].env.example[/bold] for the full template.",
            title="Configuration Error",
            border_style="red",
        ))
        sys.exit(1)

    # Login
    notifier.notify_info(f"Logging in as @{cfg.IG_USERNAME}...")
    client = InstagramClient()
    try:
        client.login()
        notifier.notify_success(f"Logged in as @{cfg.IG_USERNAME}")
    except ConnectionError as exc:
        console.print(Panel(
            f"[bold red]Proxy/IP probleem:[/bold red] {exc}\n\n"
            "Oplossingen:\n"
            "  1. Zet PROXY_URL= leeg in .env om zonder proxy te testen\n"
            "  2. Gebruik een andere proxy (bv. SOAX mobiele proxy)\n"
            "  3. Probeer mobiel 4G/5G als hotspot",
            title="IP Blocked",
            border_style="red",
        ))
        sys.exit(1)
    except ChallengeError as exc:
        notifier.notify_challenge(str(exc))
        sys.exit(1)
    except ActionBlockError as exc:
        notifier.notify_action_block(str(exc))
        sys.exit(1)
    except Exception as exc:
        notifier.notify_error(f"Login failed: {exc}")
        logging.getLogger("bot").debug(
            "Full traceback:\n%s", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        console.print("[dim]Full traceback:[/dim]")
        traceback.print_exc()
        sys.exit(1)

    engine = BotEngine(client)

    # Menu loop
    actions = {
        1: lambda: action_follow(engine),
        2: lambda: action_check_followbacks(engine),
        3: lambda: action_unfollow_non_followers(engine),
        4: lambda: action_unfollow_followed_back(engine),
        5: lambda: action_manage_requests(engine),
        6: action_stats,
        7: action_config,
        8: action_recent_activity,
        9: lambda: action_run_all(engine),
    }

    while True:
        console.print()
        print_menu()
        try:
            choice = IntPrompt.ask("[bold cyan]Choose an option[/bold cyan]", default=0)
        except KeyboardInterrupt:
            console.print("\n")
            break

        if choice == 0:
            break

        handler = actions.get(choice)
        if handler:
            try:
                handler()
            except KeyboardInterrupt:
                notifier.notify_warning("Operation interrupted by user.")
            except Exception as exc:
                notifier.notify_error(f"Unexpected error: {exc}")
                logging.getLogger("bot").exception("Unhandled exception")
        else:
            notifier.notify_warning(f"Invalid option: {choice}")

    # Cleanup
    client.logout()
    notifier.notify_info("Bot shut down. Goodbye!")


if __name__ == "__main__":
    main()
