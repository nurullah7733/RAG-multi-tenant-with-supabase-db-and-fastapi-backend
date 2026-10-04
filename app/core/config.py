import base64
import json
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "WhatsApp AI SaaS API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    supabase_url: str
    supabase_publishable_key: SecretStr = SecretStr("")

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("SUPABASE_URL must be an HTTP(S) origin")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Remote Supabase connections require HTTPS")
        return value.strip().rstrip("/")

    @field_validator("supabase_publishable_key")
    @classmethod
    def validate_public_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if not key or key.startswith("sb_publishable_"):
            return SecretStr(key)
        try:
            payload = key.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            if claims.get("role") == "anon":
                return SecretStr(key)
        except (ValueError, IndexError, AttributeError):
            pass
        raise ValueError(
            "Use a Supabase publishable/anon key, never a service-role or secret key"
        )

    # Comma-separated list of allowed CORS origins (no trailing slash).
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        origins: list[str] = []
        for raw in self.cors_origins.split(","):
            origin = raw.strip().rstrip("/")
            if origin:
                origins.append(origin)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
