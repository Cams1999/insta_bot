"""
Central configuration with defaults for the Instagram bot.
All values can be overridden via the bot_config database table at runtime.
Secrets (credentials, proxy) are loaded from .env via python-dotenv.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SESSION_DIR = BASE_DIR / "sessions"

# --- Instagram credentials (from .env) ---
IG_USERNAME: str = os.getenv("IG_USERNAME", "")
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "")
IG_2FA_SEED: str = os.getenv("IG_2FA_SEED", "")
PROXY_URL: str = os.getenv("PROXY_URL", "")

# --- Locale / timezone (match your proxy location) ---
IG_LOCALE: str = "nl_NL"
IG_COUNTRY: str = "NL"
IG_COUNTRY_CODE: int = 31
IG_TIMEZONE_OFFSET: int = 1 * 3600  # UTC+1

# --- Follow limits ---
DAILY_FOLLOW_LIMIT: int = 100
DAILY_UNFOLLOW_LIMIT: int = 100
HOURLY_FOLLOW_LIMIT: int = 12
HOURLY_UNFOLLOW_LIMIT: int = 12
DAILY_LIMIT_VARIANCE: float = 0.20  # +/- 20 %

# --- Delays (seconds) – used as Gaussian bounds ---
# Increased to reduce 429 rate limiting
FOLLOW_DELAY_MIN: float = 40.0
FOLLOW_DELAY_MAX: float = 180.0
UNFOLLOW_DELAY_MIN: float = 35.0
UNFOLLOW_DELAY_MAX: float = 120.0
LIKE_DELAY_MIN: float = 8.0
LIKE_DELAY_MAX: float = 25.0
STORY_VIEW_DELAY_MIN: float = 5.0
STORY_VIEW_DELAY_MAX: float = 18.0
CHECK_DELAY_MIN: float = 6.0
CHECK_DELAY_MAX: float = 15.0
FETCH_DELAY_MIN: float = 10.0
FETCH_DELAY_MAX: float = 30.0

# --- Session breaks ---
SESSION_BREAK_AFTER_MIN: int = 20
SESSION_BREAK_AFTER_MAX: int = 30
SESSION_BREAK_DURATION_MIN: float = 5.0 * 60   # 5 minutes in seconds
SESSION_BREAK_DURATION_MAX: float = 20.0 * 60  # 20 minutes in seconds

# --- Follow-back / unfollow timing (days) ---
FOLLOW_BACK_CHECK_DAYS: int = 5
UNFOLLOW_AFTER_DAYS: int = 30
PENDING_REQUEST_TIMEOUT_DAYS: int = 14
PENDING_REQUEST_CHECK_INTERVAL_DAYS: int = 2

# --- Active hours ---
ACTIVE_HOURS_START: int = 8   # 08:00
ACTIVE_HOURS_END: int = 23    # 23:00

# --- Warm-up ---
WARMUP_ENABLED: bool = False

# --- Drifting probability ranges ---
LIKE_CHANCE_MIN: float = 0.20
LIKE_CHANCE_MAX: float = 0.45
STORY_VIEW_CHANCE_MIN: float = 0.10
STORY_VIEW_CHANCE_MAX: float = 0.30
LIKE_COUNT_MIN: int = 1
LIKE_COUNT_MAX: int = 3
DRIFT_PERIOD_MIN: int = 8
DRIFT_PERIOD_MAX: int = 15

# --- Filters (defaults) ---
FILTER_HAS_PROFILE_PIC: bool = True
FILTER_IS_PRIVATE_ALLOWED: bool = True
FILTER_MIN_POSTS: int = 1
FILTER_MAX_POSTS: int | None = None
FILTER_MIN_FOLLOWERS: int = 0
FILTER_MAX_FOLLOWERS: int = 10_000
FILTER_MIN_FOLLOWING: int = 0
FILTER_MAX_FOLLOWING: int | None = None

# --- Database ---
DB_PATH: Path = DATA_DIR / "instabot.db"

# --- Flask ---
FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-me-in-production-!@#$%")
FLASK_HOST: str = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))
