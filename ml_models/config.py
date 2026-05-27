import os

DB_CONFIG = {
    "host": "localhost",
    "dbname": "lit_db",
    "user": "postgres",
    "password": "123456",
    "port": 5432,
}

DB_URL = f"postgresql+psycopg2://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"

# Paths are relative to this file — resolves to kirana-master-backend/ml_models/
MODELS_DIR  = os.path.join(os.path.dirname(__file__), "artifacts")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
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
