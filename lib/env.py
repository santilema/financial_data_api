from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    EDGAR_USER_AGENT: str = "FinDataAI santiago.lema@proton.me"
    EDGAR_CACHE_DIR: str = ".cache"
    model_config = SettingsConfigDict(env_file=".env.local")


env = Settings()
