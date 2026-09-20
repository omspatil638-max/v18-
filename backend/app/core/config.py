from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import List, Union


def _secret(value: Union[SecretStr, str, None]) -> str:
    """Read a credential's real value. Tolerates plain strings (tests monkeypatch them)."""
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    return str(value).strip()

# Legacy / friendly aliases accepted for LLM_PROVIDER.
_PROVIDER_ALIASES = {
    "": "none",
    "none": "none",
    "off": "none",
    "disabled": "none",
    "local": "none",          # old "local synthesizer" fabricated answers; it no longer exists
    "groq": "groq",
    "openai_compatible": "openai_compatible",
    "openai": "openai_compatible",
    "ollama": "openai_compatible",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "fake": "fake",           # tests only (APP_ENV=test)
}

# Default model per provider. openai_compatible has none on purpose: the model
# name depends on the server (Ollama, vLLM, ...), so it must be set explicitly.
DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "anthropic": "claude-sonnet-5",
}


class Settings(BaseSettings):
    # ── LLM (all configurable; no model name is hardcoded in code) ────────────
    LLM_PROVIDER: str = "none"          # none | groq | openai_compatible | anthropic
    LLM_MODEL: str = ""                 # empty -> provider default (see DEFAULT_MODELS)
    LLM_BASE_URL: str = ""              # openai_compatible only, e.g. http://localhost:11434/v1
    # Credentials are SecretStr: printing, logging or serialising settings shows
    # '**********'. Read the real value only through the accessors below.
    GROQ_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    OPENAI_API_KEY: SecretStr = SecretStr("")   # for openai_compatible endpoints (Ollama needs none)
    LLM_TIMEOUT_S: float = 120.0
    LLM_MAX_RETRIES: int = 4
    LLM_MAX_OUTPUT_TOKENS: int = 3000
    LLM_REASONING_EFFORT: str = "low"   # only sent to openai/gpt-oss-* models
    # Client-side pacing; defaults sit just under Groq's free tier (30 RPM, 8K TPM).
    LLM_RPM_LIMIT: int = 25
    LLM_TPM_LIMIT: int = 7200
    LLM_MAX_WAIT_S: float = 90.0        # longest single rate-limit wait we accept

    # Privacy mode: "off" | "redact". redact swaps emails, phone numbers, IBANs, card/tax/SSN ids and
    # URLs for placeholders before text goes to a hosted LLM, and restores them in the reply.
    PRIVACY_MODE: str = "off"

    # OCR for scanned pages (free, local, RapidOCR). Anything read this way is never marked verified.
    OCR_ENABLED: bool = True
    OCR_MAX_PAGES: int = 40             # pages OCR'd per document; the rest are reported as skipped
    OCR_RENDER_SCALE: float = 3.0       # 2.0 misreads digits (seen: "$12,000" -> "s$12,o00"); 3.0 was accurate
    OCR_LOW_CONFIDENCE: float = 0.85

    # ── Embeddings: local, free, private (fastembed). Downloads ~67 MB once on first use. ──
    EMBEDDING_PROVIDER: str = "local"   # local | none  (none = full-text search only)
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    EMBEDDING_CACHE_DIR: str = "./.cache/fastembed"

    # ── Extraction ────────────────────────────────────────────────────────────
    EXTRACTION_CHUNK_CHARS: int = 6000

    # ── Database & storage ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://contractlens:contractlens@localhost:5432/contractlens"
    STORAGE_PATH: str = "./storage"
    MAX_UPLOAD_MB: int = 20

    # ── Auth ──────────────────────────────────────────────────────────────────
    # demo  : every request acts as one shared demo user (local trial only)
    # login : email + password accounts, per-user data isolation
    AUTH_MODE: str = "auto"             # auto | login | demo. auto: sign-up first, then login (see account_service)
    ALLOW_REGISTRATION: bool = True
    SESSION_TTL_HOURS: int = 168
    COOKIE_SECURE: bool = False
    DEMO_USER_EMAIL: str = "demo@contractlens.ai"
    DEMO_USER_NAME: str = "Demo User"

    # ── Alerts ────────────────────────────────────────────────────────────────
    ALERT_LEAD_DAYS: str = "30,14,7"
    ALERT_CHECK_INTERVAL_MIN: int = 30    # how often the background scheduler looks for due alerts
    ALERT_MAX_REPEATS: int = 5            # reminders sent after the first notification, per channel, until marked read
    APP_BASE_URL: str = "http://localhost:3000"   # used for links inside emails and push notifications
    # Optional email delivery. Off unless SMTP_HOST and SMTP_FROM are set. Any SMTP account works
    # (for Gmail use smtp.gmail.com:587 with an APP PASSWORD, never your real password).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_FROM: str = ""
    SMTP_STARTTLS: bool = True
    # Browser push (Web Push / VAPID). Generate once with scripts/setup_notifications.py; both stay in .env.
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: SecretStr = SecretStr("")
    VAPID_SUBJECT: str = "mailto:admin@localhost"

    # ── App ───────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"
    APP_ENV: str = "development"        # development | production | test
    LOG_LEVEL: str = "info"
    DB_ECHO: bool = False               # log every SQL statement WITH parameters (contains contract text; debugging only)

    # Absolute path: the file must load no matter which directory the server is
    # started from (a relative ".env" silently falls back to defaults otherwise).
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def absolute_storage_path(self) -> Path:
        path = Path(self.STORAGE_PATH)
        if not path.is_absolute():
            path = Path(__file__).parent.parent.parent / self.STORAGE_PATH
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def absolute_embedding_cache(self) -> Path:
        path = Path(self.EMBEDDING_CACHE_DIR)
        if not path.is_absolute():
            path = Path(__file__).parent.parent.parent / self.EMBEDDING_CACHE_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def groq_api_key(self) -> str:
        return _secret(self.GROQ_API_KEY)

    @property
    def anthropic_api_key(self) -> str:
        return _secret(self.ANTHROPIC_API_KEY)

    @property
    def openai_api_key(self) -> str:
        return _secret(self.OPENAI_API_KEY)

    @property
    def smtp_password(self) -> str:
        return _secret(self.SMTP_PASSWORD)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.SMTP_HOST.strip() and self.SMTP_FROM.strip())

    @property
    def vapid_private_key(self) -> str:
        return _secret(self.VAPID_PRIVATE_KEY)

    @property
    def push_configured(self) -> bool:
        return bool(self.VAPID_PUBLIC_KEY.strip() and self.vapid_private_key)

    @property
    def privacy_redact(self) -> bool:
        return self.PRIVACY_MODE.strip().lower() == "redact"

    @property
    def llm_provider(self) -> str:
        return _PROVIDER_ALIASES.get(self.LLM_PROVIDER.strip().lower(), "invalid")

    @property
    def llm_model(self) -> str:
        return self.LLM_MODEL.strip() or DEFAULT_MODELS.get(self.llm_provider, "")

    @property
    def alert_lead_days(self) -> List[int]:
        days = []
        for part in self.ALERT_LEAD_DAYS.split(","):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                days.append(int(part))
        return sorted(set(days), reverse=True) or [30, 14, 7]

    def validate_for_startup(self) -> None:
        """Fail fast on configurations that would be unsafe or silently wrong."""
        if self.AUTH_MODE not in ("demo", "login", "auto"):
            raise RuntimeError("AUTH_MODE must be 'auto', 'login' or 'demo'.")
        if self.APP_ENV == "production" and self.AUTH_MODE in ("demo", "auto"):
            raise RuntimeError(
                "AUTH_MODE=demo shares one unauthenticated user and is not allowed when "
                "APP_ENV=production. Set AUTH_MODE=login."
            )
        if self.APP_ENV == "production" and not self.COOKIE_SECURE:
            raise RuntimeError("COOKIE_SECURE must be true when APP_ENV=production (serve over HTTPS).")
        if self.llm_provider == "fake" and self.APP_ENV != "test":
            raise RuntimeError("LLM_PROVIDER=fake is only allowed when APP_ENV=test.")


settings = Settings()
