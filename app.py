"""
Flask web application for the Instagram Bot.
Serves the dashboard UI and provides REST API endpoints
for account management, bot control, stats, and activity feeds.
"""

from __future__ import annotations

import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for

from bot import database as db
from bot.manager import bot_manager
import config as cfg

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = cfg.FLASK_SECRET_KEY


@app.before_request
def ensure_db():
    db.init_db()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    accounts = db.get_all_accounts()
    statuses = bot_manager.get_all_statuses()
    for acc in accounts:
        s = statuses.get(acc["id"], {})
        acc["bot_running"] = s.get("running", False)
        acc["bot_error"] = s.get("error")
    return render_template("dashboard.html", accounts=accounts)


@app.route("/accounts")
def accounts_page():
    accounts = db.get_all_accounts()
    statuses = bot_manager.get_all_statuses()
    for acc in accounts:
        s = statuses.get(acc["id"], {})
        acc["bot_running"] = s.get("running", False)
    return render_template("accounts.html", accounts=accounts)


@app.route("/accounts/<int:account_id>")
def account_detail(account_id: int):
    account = db.get_account(account_id)
    if not account:
        return redirect(url_for("accounts_page"))
    s = bot_manager.get_all_statuses().get(account_id, {})
    account["bot_running"] = s.get("running", False)
    account["bot_error"] = s.get("error")
    configs = db.get_all_config(account_id=account_id)
    return render_template("account_detail.html", account=account, configs=configs)


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


# ---------------------------------------------------------------------------
# API: Accounts
# ---------------------------------------------------------------------------

@app.route("/api/accounts", methods=["GET"])
def api_list_accounts():
    accounts = db.get_all_accounts()
    statuses = bot_manager.get_all_statuses()
    for acc in accounts:
        acc.pop("ig_password", None)
        s = statuses.get(acc["id"], {})
        acc["bot_running"] = s.get("running", False)
        acc["bot_error"] = s.get("error")
    return jsonify(accounts)


@app.route("/api/accounts", methods=["POST"])
def api_add_account():
    data = request.json or {}
    username = data.get("ig_username", "").strip()
    password = data.get("ig_password", "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    try:
        aid = db.add_account(
            ig_username=username,
            ig_password=password,
            ig_2fa_seed=data.get("ig_2fa_seed", ""),
            proxy_url=data.get("proxy_url", ""),
        )
        return jsonify({"id": aid, "message": f"Account @{username} added"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def api_delete_account(account_id: int):
    worker = bot_manager.get_worker(account_id)
    if worker and worker.is_running:
        bot_manager.stop_bot(account_id)
    db.delete_account(account_id)
    return jsonify({"message": "Account deleted"})


@app.route("/api/accounts/<int:account_id>", methods=["PUT"])
def api_update_account(account_id: int):
    data = request.json or {}
    allowed = {"ig_username", "ig_password", "ig_2fa_seed", "proxy_url", "target_username", "follow_count"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if fields:
        db.update_account(account_id, **fields)
    return jsonify({"message": "Account updated"})


# ---------------------------------------------------------------------------
# API: Bot Control
# ---------------------------------------------------------------------------

@app.route("/api/accounts/<int:account_id>/test", methods=["POST"])
def api_test_account(account_id: int):
    success, msg = bot_manager.test_account(account_id)
    return jsonify({"success": success, "message": msg})


@app.route("/api/accounts/<int:account_id>/start", methods=["POST"])
def api_start_bot(account_id: int):
    data = request.json or {}
    account = db.get_account(account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    target = data.get("target_username", account.get("target_username", "")).strip()
    count = data.get("follow_count", account.get("follow_count", 100))
    success, msg = bot_manager.start_bot(account_id, target, int(count))
    return jsonify({"success": success, "message": msg})


@app.route("/api/accounts/<int:account_id>/stop", methods=["POST"])
def api_stop_bot(account_id: int):
    success, msg = bot_manager.stop_bot(account_id)
    return jsonify({"success": success, "message": msg})


# ---------------------------------------------------------------------------
# API: Stats & Activity
# ---------------------------------------------------------------------------

@app.route("/api/accounts/<int:account_id>/stats")
def api_account_stats(account_id: int):
    return jsonify(db.get_stats(account_id=account_id))


@app.route("/api/accounts/<int:account_id>/daily-stats")
def api_daily_stats(account_id: int):
    days = request.args.get("days", 14, type=int)
    return jsonify(db.get_daily_stats(account_id=account_id, days=days))


@app.route("/api/accounts/<int:account_id>/activity")
def api_account_activity(account_id: int):
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.get_recent_activity(limit=limit, account_id=account_id))


@app.route("/api/activity")
def api_global_activity():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.get_global_recent_activity(limit=limit))


@app.route("/api/dashboard/stats")
def api_dashboard_stats():
    accounts = db.get_all_accounts()
    total = {"accounts": len(accounts), "total_followed": 0, "active_following": 0,
             "followed_back": 0, "today_followed": 0, "today_unfollowed": 0}
    for acc in accounts:
        s = db.get_stats(account_id=acc["id"])
        total["total_followed"] += s["total_followed"]
        total["active_following"] += s["active_following"]
        total["followed_back"] += s["followed_back"]
        total["today_followed"] += s["today_followed"]
        total["today_unfollowed"] += s["today_unfollowed"]
    total["followback_ratio"] = round(
        total["followed_back"] / max(total["total_followed"], 1) * 100, 1
    )
    return jsonify(total)


# ---------------------------------------------------------------------------
# API: Config (per account)
# ---------------------------------------------------------------------------

@app.route("/api/accounts/<int:account_id>/config")
def api_get_config(account_id: int):
    return jsonify(db.get_all_config(account_id=account_id))


@app.route("/api/accounts/<int:account_id>/config", methods=["PUT"])
def api_update_config(account_id: int):
    data = request.json or {}
    for key, value in data.items():
        db.set_config(key, str(value), account_id=account_id)
    return jsonify({"message": "Config updated"})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    app.run(host=cfg.FLASK_HOST, port=cfg.FLASK_PORT, debug=True)
