"""Hosted transport, budget reservations and usage are tested without paid API calls."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from builder_site import proposal, task

from browser_to_mcp.builder.cloud import OpenAIProvider
from browser_to_mcp.builder.provider import provider_from_environment
from browser_to_mcp.builder.service import generate_phase
from browser_to_mcp.jobs.models import Budgets, JobError
from browser_to_mcp.jobs.worker import execute_phase


def completed(**changes):
    return (
        dict(
            status="completed",
            error=None,
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": json.dumps(proposal())},
                    ],
                }
            ],
        )
        | changes
    )


def provider(handle, **changes):
    return OpenAIProvider(
        **(
            dict(
                api_key="PRIVATE-KEY",
                model="test-model",
                input_rate=1000000,
                output_rate=2000000,
                max_tokens=128,
                transport=httpx.MockTransport(handle),
            )
            | changes
        )
    )


def test_api_reserves_counted_tokens_and_conservative_cost_before_generation():
    calls = []

    async def handle(request):
        payload = json.loads(request.content)
        calls.append((request.url, payload))
        assert request.url.host == "api.openai.com" and request.url.scheme == "https"
        assert request.headers["Authorization"] == "Bearer PRIVATE-KEY"
        assert b"PRIVATE-KEY" not in request.content
        if request.url.path.endswith("/input_tokens"):
            assert "store" not in payload and "max_output_tokens" not in payload
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        assert payload["max_output_tokens"] == 128 and payload["store"] is False
        assert payload["text"] == {"format": {"type": "json_object"}}
        assert not payload.get("tools")
        return httpx.Response(200, json=completed())

    async def run():
        request = await provider(handle).prepare("Return a JSON proposal", {})
        assert request.token_limit == 228
        assert request.cost_microusd == 356
        assert len(calls) == 1
        reply = await request.complete(token_limit=228)
        assert reply.prompt_tokens == 100 and reply.completion_tokens == 20
        assert reply.estimated_cost_microusd == 140
        assert reply.model == "test-model" and reply.seconds >= 0
        assert calls[0][1] == {
            k: v for k, v in calls[1][1].items() if k not in {"store", "max_output_tokens"}
        }

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": ""},
        {"api_key": "PRIVATE\nKEY"},
        {"model": "sk-PRIVATE"},
        {"model": ""},
        {"model": "bad model"},
        {"input_rate": 0},
        {"output_rate": -1},
        {"input_rate": True},
        {"max_tokens": 0},
    ],
)
def test_invalid_api_configuration_is_rejected_without_leaking_values(change):
    with pytest.raises(JobError) as raised:
        provider(lambda _: None, **change)
    assert raised.value.code == "provider_configuration"
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "incomplete"},
        {"error": {"message": "PRIVATE-KEY"}},
        {"output": [{"type": "function_call", "name": "publish"}]},
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "refusal", "refusal": "PRIVATE refusal"},
                    ],
                }
            ]
        },
        {"output": []},
        {"usage": {"input_tokens": 101, "output_tokens": 20, "total_tokens": 121}},
        {"usage": {"input_tokens": 100, "output_tokens": 129, "total_tokens": 229}},
        {"usage": {"input_tokens": 100, "output_tokens": True, "total_tokens": 101}},
        {"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 1}},
    ],
)
def test_api_refusals_truncation_and_inconsistent_usage_fail_safely(change):
    async def handle(request):
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        return httpx.Response(200, json=completed(**change))

    async def run():
        request = await provider(handle).prepare("JSON proposal", {})
        with pytest.raises(JobError) as raised:
            await request.complete(token_limit=request.token_limit)
        assert raised.value.code == "provider_response"
        assert "PRIVATE" not in str(raised.value)

    asyncio.run(run())


@pytest.mark.parametrize("status", [307, 401, 429, 500])
def test_hosted_requests_never_retry_or_follow_redirects(status):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Location": "https://evil.example"}, json={"message": "PRIVATE-KEY"}
        )

    async def run():
        with pytest.raises(JobError) as raised:
            await provider(handle).prepare("JSON proposal", {})
        assert raised.value.code == "provider_unavailable"
        assert "PRIVATE" not in str(raised.value)

    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "budget,value,dispatch",
    [
        ("model_requests", 0, False),
        ("tokens", 227, False),
        ("cost_microusd", 355, False),
        ("cost_microusd", 356, True),
    ],
)
def test_hosted_generation_obeys_job_limits_and_records_estimates(
    store,
    catalog_site,
    budget,
    value,
    dispatch,
):
    _, base = catalog_site
    calls = []

    async def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        return httpx.Response(200, json=completed())

    limits = dict(browser_actions=4, model_requests=1, tokens=228, cost_microusd=356)
    limits[budget] = value
    initial = store.start_task(str(uuid4()), task(base), Budgets(**limits), generation="provider")
    store.claim()

    async def handle_phase(context, job):
        return await generate_phase(context, job, provider=provider(handle))

    asyncio.run(execute_phase(store, initial["id"], handle_phase))
    result = store.read(initial["id"])
    assert ("/v1/responses" in calls) is dispatch
    if dispatch:
        assert result["state"] == "ready_for_review", result
        assert result["usage"]["cost_microusd"] == 356
        assert result["report"]["estimated_cost_microusd"] == 140
        assert result["report"]["api_cost_microusd"] is None
        assert result["report"]["cost_basis"] == "configured_rates"
        assert result["report"]["model"] == "test-model"
    else:
        assert result["state"] == "failed"
        assert result["error"]["code"] == "budget_" + budget
    assert "PRIVATE" not in json.dumps(result)
    assert b"PRIVATE-KEY" not in store.database.read_bytes()


def test_api_rates_round_up_and_provider_selection_is_explicit(monkeypatch):
    p = provider(lambda _: None, input_rate=1, output_rate=1)
    assert p.cost(1, 1) == 2
    for name in ["BROWSER_TO_MCP_PROVIDER", "BROWSER_TO_MCP_MODEL_URL"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-KEY")
    with pytest.raises(JobError):
        provider_from_environment()  # possessing a key does not opt a task into cloud use
    monkeypatch.setenv("BROWSER_TO_MCP_PROVIDER", "openai")
    monkeypatch.setenv("BROWSER_TO_MCP_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("BROWSER_TO_MCP_INPUT_MICROUSD_PER_MILLION", "1000000")
    monkeypatch.setenv("BROWSER_TO_MCP_OUTPUT_MICROUSD_PER_MILLION", "2000000")
    assert isinstance(provider_from_environment(), OpenAIProvider)
    monkeypatch.setenv("BROWSER_TO_MCP_INPUT_MICROUSD_PER_MILLION", "invalid")
    with pytest.raises(JobError):
        provider_from_environment()
