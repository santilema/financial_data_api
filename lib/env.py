from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ALPHA_VANTAGE_API_KEY: str
    model_config = SettingsConfigDict(env_file=".env.local")


env = Settings()
