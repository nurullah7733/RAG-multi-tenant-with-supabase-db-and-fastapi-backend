"""Stateless text generation boundary (Phase 3.1), separate from embeddings."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol

import httpx
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.config import Settings, settings
from app.core.errors import AppError


class GenerationError(AppError):
    status_code = 503
    code = "generation_unavailable"
    message = "The AI generation service is temporarily unavailable."


class GenerationConfigurationError(GenerationError):
    code = "generation_not_configured"
    message = "Configure AI_MODEL and the selected provider API key on the backend."


class GenerationMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000, repr=False)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be blank")
        return value


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    messages: tuple[GenerationMessage, ...] = Field(
        min_length=1, max_length=100, repr=False
    )


class GenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    model: str
    text: str = Field(repr=False)
    finish_reason: Literal["stop", "length"]


class AIProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class _ChatProvider:
    name: str
    url: str
    token_parameter = "max_tokens"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: SecretStr,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self.client = client
        self._api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        payload = {
            "model": self.model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": False,
            self.token_parameter: self.max_output_tokens,
        }
        if self.name == "openai":
            payload["store"] = False
        try:
            # Total deadline also bounds providers that send keep-alive bytes.
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}"
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                )
        except (httpx.RequestError, TimeoutError):
            raise GenerationError() from None
        if not response.is_success:
            # No raw response, prompts, credentials or exception context exposed.
            raise GenerationError()
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            content = message["content"]
            reason = choice["finish_reason"]
            if (
                message.get("role") != "assistant"
                or message.get("refusal")
                or message.get("tool_calls")
                or not isinstance(content, str)
                or not content.strip()
                or reason not in ("stop", "length")
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise GenerationError() from None
        return GenerationResult(
            provider=self.name,
            model=self.model,
            text=content,
            finish_reason=reason,
        )


class OpenAIProvider(_ChatProvider):
    name = "openai"
    url = "https://api.openai.com/v1/chat/completions"
    token_parameter = "max_completion_tokens"


class MistralProvider(_ChatProvider):
    name = "mistral"
    url = "https://api.mistral.ai/v1/chat/completions"


class DeepSeekProvider(_ChatProvider):
    name = "deepseek"
    url = "https://api.deepseek.com/chat/completions"


class AIService:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return await self._provider.generate(request)


def create_ai_service(client: httpx.AsyncClient, config: Settings) -> AIService:
    providers = {
        "openai": (OpenAIProvider, config.openai_api_key),
        "mistral": (MistralProvider, config.mistral_api_key),
        "deepseek": (DeepSeekProvider, config.deepseek_api_key),
    }
    provider_class, key = providers[config.ai_provider]
    if not config.ai_model or not key.get_secret_value().strip():
        raise GenerationConfigurationError()
    return AIService(
        provider_class(
            client,
            api_key=SecretStr(key.get_secret_value().strip()),
            model=config.ai_model,
            max_output_tokens=config.ai_max_output_tokens,
            timeout_seconds=config.ai_timeout_seconds,
        )
    )


async def get_generation_client() -> AsyncIterator[httpx.AsyncClient]:
    # Dedicated client: no Supabase headers or auth state shared with AI vendors.
    async with httpx.AsyncClient(
        timeout=settings.ai_timeout_seconds, follow_redirects=False
    ) as client:
        yield client


def get_ai_service(
    client: Annotated[httpx.AsyncClient, Depends(get_generation_client)],
) -> AIService:
    return create_ai_service(client, settings)
