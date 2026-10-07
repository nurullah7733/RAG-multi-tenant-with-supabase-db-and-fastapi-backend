import base64
import json
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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

    knowledge_bucket_name: Literal["knowledge-documents"] = "knowledge-documents"
    max_document_size_mb: int = Field(default=20, ge=1, le=20)

    # Chunking strategy (Phase 2.5). Overlap at most half the size; minimum
    # chunk threshold strictly below the target size.
    document_chunk_size: int = Field(default=1000, ge=200, le=4000)
    document_chunk_overlap: int = Field(default=150, ge=0, le=2000)
    document_min_chunk_chars: int = Field(default=200, ge=50, le=1000)

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if 2 * self.document_chunk_overlap > self.document_chunk_size:
            raise ValueError(
                "DOCUMENT_CHUNK_OVERLAP must be at most half of DOCUMENT_CHUNK_SIZE"
            )
        if self.document_min_chunk_chars >= self.document_chunk_size:
            raise ValueError(
                "DOCUMENT_MIN_CHUNK_CHARS must be smaller than DOCUMENT_CHUNK_SIZE"
            )
        return self

    # Embedding configuration (Phase 2.7). The dimension must match the
    # pgvector column created by the knowledge_chunks migration.
    embedding_provider: Literal["openai"] = "openai"
    embedding_model: str = Field(
        default="text-embedding-3-small", min_length=1, max_length=100
    )
    embedding_dimensions: int = Field(default=1536, ge=64, le=3072)
    openai_api_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_embeddings(self) -> "Settings":
        if not self.openai_api_key.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY is required for document indexing")
        if self.embedding_dimensions != 1536:
            raise ValueError(
                "EMBEDDING_DIMENSIONS must match the knowledge_chunks vector column (1536)"
            )
        return self

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
