from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CONTRACT_VERSION = "0.2.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SPX_", extra="ignore")

    # Origins allowed to call the API from a browser (comma separated).
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    log_level: str = "INFO"
    # Build the five demo targets' spectra in the background at start-up so the Spectrum page is ready for the first visitor (uses the archive).
    prewarm_spectra: bool = False
    # Ask the archive a tiny question now and then (in the background) so the app can show whether it is answering. Off by default: no network in tests.
    archive_probe: bool = False
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
    llm_provider_3: str = ""
    llm_api_key_3: SecretStr = SecretStr("")
    llm_model_3: str = ""
    llm_base_url_3: str = ""
    # Per provider. A flash-lite model normally answers in 1 to 2 seconds; waiting longer rarely helps, so fail over to the next one.
    llm_timeout_s: float = 12.0
    # Protect the free-tier quota; when a limit is hit the visitor gets the friendly automated summary instead of an error.
    # Free tiers typically allow about 10 to 15 requests a minute and a few hundred a day, depending on the model: check yours in
    # Google AI Studio and set these to match. The real ceiling is the provider's, not ours.
    chat_limit_per_client: int = 60
    chat_limit_global_daily: int = 1500
    chat_limit_per_minute: int = 10


settings = Settings()
