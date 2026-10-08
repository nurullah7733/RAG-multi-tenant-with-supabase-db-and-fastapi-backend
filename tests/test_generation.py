import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.generation import (
    GenerationConfigurationError,
    GenerationError,
    GenerationMessage,
    GenerationRequest,
    create_ai_service,
)

REQUEST = GenerationRequest(
    messages=(GenerationMessage(role="user", content="বাংলা প্রশ্ন"),)
)


def config(provider="openai", **overrides):
    values = {
        "_env_file": None,
        "supabase_url": "https://supabase.example.test",
        "openai_api_key": "test-openai",
        "mistral_api_key": "test-mistral",
        "deepseek_api_key": "test-deepseek",
        "ai_provider": provider,
        "ai_model": "test-model",
    }
    values.update(overrides)
    return Settings(**values)


def response_payload(content="বাংলা উত্তর", reason="stop"):
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": reason,
            }
        ]
    }


def run(handler, settings=None):
    async def call():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            return await create_ai_service(client, settings or config()).generate(
                REQUEST
            )

    return asyncio.run(call())


@pytest.mark.parametrize(
    "provider,host,token_field",
    [
        ("openai", "api.openai.com", "max_completion_tokens"),
        ("mistral", "api.mistral.ai", "max_tokens"),
        ("deepseek", "api.deepseek.com", "max_tokens"),
    ],
)
def test_provider_contract(provider, host, token_field):
    def handler(request):
        assert request.url.host == host
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == f"Bearer test-{provider}"
        assert "apikey" not in request.headers
        body = json.loads(request.content)
        assert body["messages"] == [{"role": "user", "content": "বাংলা প্রশ্ন"}]
        assert body["model"] == "test-model"
        assert body[token_field] == 1024
        assert body["stream"] is False
        if provider == "openai":
            assert body["store"] is False
            assert "max_tokens" not in body
        else:
            assert "max_completion_tokens" not in body
        return httpx.Response(200, json=response_payload())

    result = run(handler, config(provider))
    assert result.text == "বাংলা উত্তর"
    assert result.provider == provider
    assert result.model == "test-model"
    assert result.finish_reason == "stop"


@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 429, 500, 503])
def test_failure_and_redirect_do_not_leak_or_retry(status, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://untrusted.example"},
            text="secret-private-payload",
        )

    with pytest.raises(GenerationError) as error:
        run(handler)
    assert len(calls) == 1
    assert "secret-private-payload" not in str(error.value) + caplog.text
    assert "test-openai" not in str(error.value) + caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [None]},
        response_payload(None),
        response_payload(" "),
        response_payload("answer", "content_filter"),
        response_payload("answer", "tool_calls"),
    ],
)
def test_bad_response_is_safe(payload):
    with pytest.raises(GenerationError):
        run(lambda request: httpx.Response(200, json=payload))


def test_invalid_json():
    with pytest.raises(GenerationError):
        run(lambda request: httpx.Response(200, text="not json"))


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failure(exception):
    def handler(request):
        raise exception("private-provider-detail", request=request)

    with pytest.raises(GenerationError) as error:
        run(handler)
    assert "private-provider-detail" not in str(error.value)
    assert error.value.__suppress_context__


def test_total_deadline():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response_payload())

    settings = config().model_copy(update={"ai_timeout_seconds": 0.01})
    with pytest.raises(GenerationError):
        run(handler, settings)


def test_truncation_is_explicit():
    assert (
        run(
            lambda request: httpx.Response(
                200, json=response_payload("partial", "length")
            )
        ).finish_reason
        == "length"
    )


@pytest.mark.parametrize("provider", ["mistral", "deepseek"])
def test_missing_selected_key_fails_before_network(provider):
    settings = config(provider, **{f"{provider}_api_key": " "})
    with pytest.raises(GenerationConfigurationError):
        run(lambda request: pytest.fail("network must not run"), settings)


def test_missing_model_and_unselected_keys():
    with pytest.raises(GenerationConfigurationError):
        run(lambda request: pytest.fail("network must not run"), config(ai_model=""))
    result = run(
        lambda request: httpx.Response(200, json=response_payload()),
        config(mistral_api_key="", deepseek_api_key=""),
    )
    assert result.provider == "openai"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ai_provider": "unknown"},
        {"ai_model": " "},
        {"ai_max_output_tokens": 0},
        {"ai_timeout_seconds": 0},
    ],
)
def test_invalid_config(overrides):
    with pytest.raises(ValidationError):
        config(**overrides)


def test_input_validation_and_repr():
    with pytest.raises(ValidationError):
        GenerationRequest(messages=())
    with pytest.raises(ValidationError):
        GenerationMessage(role="user", content=" ")
    assert "বাংলা" not in repr(REQUEST)
    assert "test-mistral" not in repr(config())


def test_generation_selection_preserves_embeddings():
    settings = config("mistral")
    assert settings.embedding_provider == "openai"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
