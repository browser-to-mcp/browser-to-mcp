import asyncio
import json

import httpx
import pytest

from browser_to_mcp.builder.provider import LlamaCppProvider
from browser_to_mcp.jobs.models import JobError


def test_exact_token_reservation_local_transport_and_secret_isolation():
    calls = []

    async def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        assert request.headers["authorization"] == "Bearer PRIVATE-KEY"
        assert b"PRIVATE-KEY" not in request.content
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Rendered task"})
        if request.url.path == "/tokenize":
            assert body == dict(
                content="Rendered task", add_special=True, parse_special=True, with_pieces=False
            )
            return httpx.Response(200, json={"tokens": [1, 2, 3]})
        assert body["prompt"] == [1, 2, 3]
        assert body["n_predict"] == 128
        assert body["stream"] is False and body["cache_prompt"] is False
        assert body["json_schema"] == {"type": "object"}
        return httpx.Response(
            200,
            json={
                "content": '{"status":"unsupported","reason":"ambiguous"}',
                "tokens_evaluated": 3,
                "tokens_predicted": 20,
                "truncated": False,
                "stop_type": "eos",
            },
        )

    async def run():
        provider = LlamaCppProvider(
            "http://127.0.0.1:8080",
            api_key="PRIVATE-KEY",
            max_tokens=128,
            transport=httpx.MockTransport(handle),
        )
        prepared = await provider.prepare("Public request", {"type": "object"})
        assert prepared.token_limit == 131 and prepared.cost_microusd == 0
        assert len(calls) == 2  # no inference until the caller reserves this budget
        reply = await prepared.complete(token_limit=131)
        assert reply.prompt_tokens == 3 and reply.completion_tokens == 20
        assert reply.seconds >= 0

    asyncio.run(run())
    assert [path for path, _ in calls] == ["/apply-template", "/tokenize", "/completion"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.example.com",
        "http://localhost:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:8080/v1",
        "http://user:PRIVATE@127.0.0.1:8080",
        "http://127.0.0.1:8080?key=PRIVATE",
        "http://127.0.0.1:8080/#PRIVATE",
    ],
)
def test_endpoint_is_operator_configured_literal_loopback_only(endpoint):
    with pytest.raises(JobError) as raised:
        LlamaCppProvider(endpoint)
    assert raised.value.code == "provider_configuration"
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(307, headers={"Location": "https://evil.example"}),
        httpx.Response(401, json={"error": "PRIVATE-KEY"}),
        httpx.Response(200, content="PRIVATE malformed JSON"),
        httpx.Response(200, json=["not an object"]),
        httpx.Response(200, content=b"x" * 1048577),
        httpx.Response(200, json={"prompt": "x" * 131073}),
    ],
)
def test_provider_fails_safely_without_retries_or_redirects(response):
    requests = []

    async def handle(request):
        requests.append(request)
        return response

    async def run():
        provider = LlamaCppProvider("http://127.0.0.1:8080", transport=httpx.MockTransport(handle))
        with pytest.raises(JobError) as raised:
            await provider.prepare("Task", {})
        assert "PRIVATE" not in str(raised.value)

    asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"tokens_predicted": 129},
        {"tokens_predicted": True},
        {"tokens_evaluated": 2},
        {"tokens_evaluated": False},
        {"truncated": True},
        {"stop_type": "limit"},
        {"content": None},
        {"content": "x" * 65537},
    ],
)
def test_incomplete_or_inconsistent_completion_is_rejected(change):
    async def handle(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Task"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1]})
        return httpx.Response(
            200,
            json=dict(
                content="{}",
                tokens_predicted=1,
                tokens_evaluated=1,
                truncated=False,
                stop_type="eos",
            )
            | change,
        )

    async def run():
        provider = LlamaCppProvider(
            "http://127.0.0.1:8080", max_tokens=128, transport=httpx.MockTransport(handle)
        )
        prepared = await provider.prepare("Task", {})
        with pytest.raises(JobError) as raised:
            await prepared.complete(token_limit=prepared.token_limit)
        assert raised.value.code == "provider_response"

    asyncio.run(run())


@pytest.mark.parametrize("tokens", [[], [True], [-1], ["1"], "not a list"])
def test_tokenization_must_return_bounded_integer_ids(tokens):
    async def handle(request):
        return httpx.Response(200, json={"prompt": "Task", "tokens": tokens})

    async def run():
        provider = LlamaCppProvider("http://127.0.0.1:8080", transport=httpx.MockTransport(handle))
        with pytest.raises(JobError) as raised:
            await provider.prepare("Task", {})
        assert raised.value.code == "provider_response"

    asyncio.run(run())


def test_configuration_is_loaded_only_from_worker_environment(monkeypatch):
    monkeypatch.delenv("BROWSER_TO_MCP_MODEL_URL", raising=False)
    with pytest.raises(JobError):
        LlamaCppProvider.from_environment()
    monkeypatch.setenv("BROWSER_TO_MCP_MODEL_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("BROWSER_TO_MCP_MODEL_MAX_TOKENS", "not an integer")
    with pytest.raises(JobError):
        LlamaCppProvider.from_environment()
