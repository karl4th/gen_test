from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    webhook_secret: str = "postgres"
    provider_alpha_url: str = "http://127.0.0.1:8001"
    provider_beta_url: str = "http://127.0.0.1:8002"
    public_callback_base_url: str = "http://127.0.0.1:8000"
    provider_timeout: int = 60
    provider_http_timeout_seconds: float = 5.0
    watchdog_interval_seconds: float = 1.0

    def provider_url(self, name: str) -> str:
        if name == "alpha":
            return self.provider_alpha_url
        if name == "beta":
            return self.provider_beta_url
        raise ValueError(f"Unknown provider: {name}")
