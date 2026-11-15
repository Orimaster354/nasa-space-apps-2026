from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CONTRACT_VERSION = "0.2.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SPX_", extra="ignore")

    # Origins allowed to call the API from a browser (comma separated).
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    log_level: str = "INFO"
    data_dir: str = "data"
    # If set, this folder (the frontend) is served at "/" so the app runs from one origin (no CORS, one URL for judges).
    frontend_dir: str = ""
    # SIA2 collections searched for observations (comma separated). A new data release is a config change.
    sia_collections: str = "spherex_dr1,spherex_dr1_deep,spherex_qr3,spherex_qr3_deep"

    # AI companion. Keys are SecretStr so they never show up in repr or logs. Leave a provider blank to disable it.
    # Provider 1 is tried first, then provider 2, then the built-in automated summary. "gemini" or "openai_compatible".
    llm_provider_1: str = ""
    llm_api_key_1: SecretStr = SecretStr("")
    llm_model_1: str = ""
    llm_base_url_1: str = ""
    llm_provider_2: str = ""
    llm_api_key_2: SecretStr = SecretStr("")
    llm_model_2: str = ""
    llm_base_url_2: str = ""
    llm_timeout_s: float = 25.0
    # Protect the free-tier quota: after this many AI messages a visitor gets automated summaries instead.
    chat_limit_per_client: int = 15
    chat_limit_global_daily: int = 500


settings = Settings()
