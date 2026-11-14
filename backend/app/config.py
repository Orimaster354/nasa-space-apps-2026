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


settings = Settings()
