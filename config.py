"""Master backend configuration — single DB (lit_db), local ML models."""
import os
from functools import lru_cache
from dataclasses import dataclass
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))


@dataclass(frozen=True)
class Settings:
    # ── Server ──────────────────────────────────────────────────────────────
    host: str
    port: int
    debug: bool

    # ── Single database (lit_db) ─────────────────────────────────────────────
    db_url: str

    # ── Kirana AI ────────────────────────────────────────────────────────────
    kirana_api_key: str
    ml_results_dir: str    # path to ml_models/results/ CSVs  (local copy)
    ml_artifacts_dir: str  # path to ml_models/artifacts/ .pkl (local copy)

    # ── POS auth (JWT) — still needed for cashier login ──────────────────────
    pos_secret_key: str
    pos_algorithm: str
    pos_token_expire_minutes: int

    # ── WhatsApp ─────────────────────────────────────────────────────────────
    whatsapp_api_base_url: str
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    whatsapp_business_account_id: str
    whatsapp_verify_token: str

    # ── Mistral AI ───────────────────────────────────────────────────────────
    mistral_api_key: str
    mistral_model: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        host=os.getenv("MASTER_HOST", "0.0.0.0"),
        port=int(os.getenv("MASTER_PORT", "9000")),
        debug=os.getenv("MASTER_DEBUG", "false").lower() == "true",

        # Single DB — everything lives in lit_db
        db_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://postgres:123456@localhost:5432/lit_db",
        ),

        kirana_api_key=os.getenv("KIRANA_API_KEY", "kirana-dev-key"),

        # Local ML model copies inside the master backend directory
        ml_results_dir=os.getenv(
            "ML_RESULTS_DIR",
            os.path.join(_HERE, "ml_models", "results"),
        ),
        ml_artifacts_dir=os.getenv(
            "ML_ARTIFACTS_DIR",
            os.path.join(_HERE, "ml_models", "artifacts"),
        ),

        pos_secret_key=os.getenv("POS_SECRET_KEY", "pos-super-secret-change-in-prod"),
        pos_algorithm=os.getenv("POS_ALGORITHM", "HS256"),
        pos_token_expire_minutes=int(os.getenv("POS_TOKEN_EXPIRE_MINUTES", "43200")),

        whatsapp_api_base_url=os.getenv(
            "WHATSAPP_API_BASE_URL", "https://graph.facebook.com/v25.0"
        ),
        whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        whatsapp_business_account_id=os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ""),
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", "kirana_verify_token"),

        mistral_api_key=os.getenv("MISTRAL_API_KEY", ""),
        mistral_model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
    )
