import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class BackendSettings(BaseSettings):
    host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    port: int = Field(default=8000, alias="BACKEND_PORT")
    secret_token: str = Field(default="supersecret", alias="BACKEND_SECRET")
    log_level: str = Field(default="INFO", alias="BACKEND_LOG_LEVEL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = BackendSettings()
