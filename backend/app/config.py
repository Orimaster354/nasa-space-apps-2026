from pydantic_settings import BaseSettings, SettingsConfigDict

CONTRACT_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SPX_", extra="ignore")

    # Origins allowed to call the API from a browser (comma separated).
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    log_level: str = "INFO"
    data_dir: str = "data"


settings = Settings()
