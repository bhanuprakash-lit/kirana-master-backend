import os
from urllib.parse import urlparse, parse_qs, unquote

_HERE = os.path.dirname(__file__)


def _db_config_from_url(url: str) -> dict:
    """Parse a (SQLAlchemy or plain) Postgres URL into psycopg2 connect kwargs.

    Handles the `+psycopg2`/`+asyncpg` dialect suffixes and Azure SSL: if the
    URL carries `?sslmode=` (or the host is an Azure Postgres host), it is
    forwarded so psycopg2.connect(**DB_CONFIG) works against managed Postgres.
    """
    dsn = url
    for prefix in ("postgresql+psycopg2://", "postgres+psycopg2://",
                   "postgresql+asyncpg://", "postgres://"):
        if dsn.startswith(prefix):
            dsn = "postgresql://" + dsn[len(prefix):]
            break
    u = urlparse(dsn)
    q = parse_qs(u.query)
    cfg = {
        "host": u.hostname or "localhost",
        "dbname": (u.path or "").lstrip("/") or "lit_db",
        "user": unquote(u.username) if u.username else "postgres",
        "password": unquote(u.password) if u.password else "",
        "port": u.port or 5432,
    }
    sslmode = (q.get("sslmode") or [None])[0] or os.getenv("PGSSLMODE")
    if not sslmode and "azure" in (u.hostname or "").lower():
        sslmode = "require"
    if sslmode:
        cfg["sslmode"] = sslmode
    return cfg


# DB connection — env-driven so the nightly training subprocess (which inherits
# the FastAPI process env) connects to the SAME database as the app. Falls back
# to a local dev DB only when DATABASE_URL is unset.
_DATABASE_URL = os.getenv("DATABASE_URL")
if _DATABASE_URL:
    DB_CONFIG = _db_config_from_url(_DATABASE_URL)
    DB_URL = _DATABASE_URL
else:
    DB_CONFIG = {
        "host": "localhost",
        "dbname": "lit_db",
        "user": "postgres",
        "password": "123456",
        "port": 5432,
    }
    DB_URL = "postgresql+psycopg2://postgres:123456@localhost:5432/lit_db"

# Output dirs — env-driven so they match the app's MLAdapter (config.py
# ml_results_dir / ml_artifacts_dir). On Azure these should point at a mounted
# Azure File Share so trained models survive container restarts.
MODELS_DIR  = os.getenv("ML_ARTIFACTS_DIR", os.path.join(_HERE, "artifacts"))
RESULTS_DIR = os.getenv("ML_RESULTS_DIR",   os.path.join(_HERE, "results"))
os.makedirs(MODELS_DIR,  exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Stockout risk thresholds (days of supply)
STOCKOUT_HORIZONS = [3, 7, 21, 30]

# SKU velocity percentile thresholds
FAST_PERCENTILE = 75   # top 25% by velocity = fast
SLOW_PERCENTILE = 25   # bottom 25% by velocity = slow

# Margin threshold for "high profit"
HIGH_MARGIN_PERCENTILE = 75

# Dead stock: no significant sale in this many days
DEAD_STOCK_DAYS = 21
DEAD_STOCK_UNITS_THRESHOLD = 2  # avg units/day below this = dead candidate

# Safety stock multiplier (z-score for 95% service level)
SAFETY_STOCK_Z = 1.645

RANDOM_SEED = 42
