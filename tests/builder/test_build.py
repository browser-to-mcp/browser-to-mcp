import asyncio
import json
from functools import partial
from uuid import uuid4

import pytest
from builder_site import expected, proposal, task

from browser_to_mcp.builder.provider import ModelReply
from browser_to_mcp.builder.service import generate_phase
from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.jobs.models import Budgets
from browser_to_mcp.jobs.worker import execute_phase
from browser_to_mcp.replay import replay_workflow

LIMITS = Budgets(browser_actions=10, model_requests=1, tokens=10000)


class ControlledProvider:
    """Fixed model answers for reproducible orchestration checks, not real AI inference."""

    token_limit = 2000
    cost_microusd = 0

    def __init__(self, answer, *, gate=None):
        self.answer, self.gate = answer, gate
        self.calls = 0
        self.prompt = None

    async def prepare(self, prompt, schema):
        self.prompt = prompt
        assert schema["additionalProperties"] is False
        return self

    async def complete(self, *, token_limit):
        self.calls += 1
        assert token_limit == self.token_limit
        if self.gate:
            await self.gate.wait()
        return ModelReply(json.dumps(self.answer), 1500, 200, 0.001)


def build(store, request, provider, limits=LIMITS):
    initial = store.start_task(str(uuid4()), request, limits)
    store.claim()
    asyncio.run(execute_phase(store, initial["id"], partial(generate_phase, provider=provider)))
    return store.read(initial["id"])


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
def test_three_layouts_build_review_and_replay_changing_inputs(
    store,
    catalog_site,
    tmp_path,
    layout,
):
    site, base = catalog_site
    provider = ControlledProvider(proposal(layout))
    ready = build(store, task(base, layout), provider)
    assert ready["state"] == "ready_for_review", ready
    assert ready["usage"]["browser_actions"] == 4
    assert ready["usage"]["model_requests"] == 1
    assert ready["usage"]["tokens"] == 2000
    assert ready["report"]["checks"] == ["schema", "policy", "compiler", "first_page_preview"]
    assert ready["report"]["provider"] == "controlled"
    assert ready["report"]["api_cost_microusd"] == 0
    assert "NEVER-SEND" not in provider.prompt
    assert not list(store.directory.glob("*.yaml"))
    loaded, _ = check_candidate(store.read_artifact(ready["id"], "draft").decode())
    store.answer(ready["id"], ready["question"]["id"], ready["revision"], "keep")
    store.claim()
    asyncio.run(execute_phase(store, ready["id"]))
    assert store.read(ready["id"])["state"] == "completed"
    assert provider.calls == 1

    async def replay():
        full = await replay_workflow(loaded, ["query=notebook"], trace_dir=tmp_path / "traces")
        assert full.outputs == {
            "result": expected(base),
            "collection": dict(pages_read=2, complete=True, stop_reason="end"),
        }
        assert full.trace_path
        trace = json.loads(open(full.trace_path).read())
        assert trace["metadata"]["manifest_digest"] == loaded.manifest_digest
        changed = await replay_workflow(loaded, ["query=red"])
        assert changed.outputs["result"] == expected(base, (2,))
        assert changed.outputs["collection"]["pages_read"] == 1
        empty = await replay_workflow(loaded, ["query=empty"])
        assert empty.outputs["result"] == []
        assert empty.outputs["collection"]["complete"] is True

    asyncio.run(replay())
    assert len(site.hits) == 5  # build page, two full pages, changed input, empty


def test_missing_shape_asks_before_browser_or_model_and_resumes(store, catalog_site):
    site, base = catalog_site
    request = task(base, shape=None, max_pages=1, allow_empty=False)
    provider = ControlledProvider(proposal(paged=False, empty=False))
    waiting = build(store, request, provider)
    assert waiting["state"] == "waiting_for_input"
    assert waiting["question"]["kind"] == "output_shape"
    assert site.hits == [] and provider.calls == 0
    store.answer(waiting["id"], waiting["question"]["id"], waiting["revision"], "list")
    store.claim()
    asyncio.run(execute_phase(store, waiting["id"], partial(generate_phase, provider=provider)))
    assert store.read(waiting["id"])["state"] == "ready_for_review"
    assert provider.calls == 1


@pytest.mark.parametrize(
    "mode,code",
    [
        ("blocked", "page_unavailable"),
        ("login", "task_login"),
        ("large", "page_too_large"),
        ("broken", "preview_failed"),
    ],
)
def test_unreadable_pages_and_bad_selectors_fail_without_a_draft(store, catalog_site, mode, code):
    site, base = catalog_site
    site.mode = mode
    provider = ControlledProvider(proposal())
    result = build(store, task(base), provider)
    assert result["state"] == "failed" and result["error"]["code"] == code, result
    assert result["artifact"] is None
    assert provider.calls == (1 if mode == "broken" else 0)
    store.maintain()
    assert not list(store.directory.glob("*.candidate"))


@pytest.mark.parametrize(
    "budget,value,expected_calls",
    [
        ("browser_actions", 0, 0),
        ("browser_actions", 2, 0),
        ("browser_actions", 3, 1),
        ("model_requests", 0, 0),
        ("tokens", 1999, 0),
        ("output_bytes", 1, 0),
    ],
)
def test_resource_limits_gate_operations(store, catalog_site, budget, value, expected_calls):
    _, base = catalog_site
    provider = ControlledProvider(proposal())
    result = build(store, task(base), provider, LIMITS.model_copy(update={budget: value}))
    assert result["state"] == "failed", result
    assert result["error"]["code"] == "budget_" + budget
    assert provider.calls == expected_calls
    assert result["artifact"] is None


def test_injected_page_text_cannot_expand_permissions_or_publish(store, catalog_site):
    site, base = catalog_site
    site.mode = "injection"
    provider = ControlledProvider(
        proposal() | {"permissions": {"allowed_domains": ["evil.example"]}}
    )
    result = build(store, task(base), provider)
    assert result["error"]["code"] == "invalid_proposal"
    assert "Ignore the user" in provider.prompt  # adversarial data reached the model seam
    assert "NEVER-SEND" not in provider.prompt
    assert result["artifact"] is None
    assert len(site.hits) == 1
    assert not list(store.directory.glob("*.yaml"))


def test_cancellation_during_model_request_cannot_save_a_draft(store, catalog_site):
    _, base = catalog_site

    async def run():
        provider = ControlledProvider(proposal(), gate=asyncio.Event())
        initial = store.start_task(str(uuid4()), task(base), LIMITS)
        store.claim()
        phase = asyncio.create_task(
            execute_phase(
                store,
                initial["id"],
                partial(generate_phase, provider=provider),
            )
        )
        for _ in range(200):
            if provider.calls:
                break
            await asyncio.sleep(0.02)
        assert provider.calls == 1
        store.cancel(initial["id"])
        provider.gate.set()
        await phase
        result = store.read(initial["id"])
        assert result["state"] == "cancelled" and result["artifact"] is None
        assert not list(store.directory.glob("*.draft.candidate"))

    asyncio.run(run())


def test_missing_model_configuration_fails_before_browser(store, catalog_site, monkeypatch):
    site, base = catalog_site
    monkeypatch.delenv("BROWSER_TO_MCP_MODEL_URL", raising=False)
    initial = store.start_task(str(uuid4()), task(base), LIMITS)
    store.claim()
    asyncio.run(execute_phase(store, initial["id"]))
    assert store.read(initial["id"])["error"]["code"] == "provider_configuration"
    assert site.hits == []


def test_one_record_without_inputs_uses_normal_object_output(store, catalog_site):
    _, base = catalog_site
    request = task(base, shape="object", max_pages=1, allow_empty=False, unique_by=None)
    request["start_url"] = base + "/books/?q=red"
    request["inputs"] = {}
    plan = proposal(paged=False, empty=False, unique=False)
    plan["bindings"] = {}
    provider = ControlledProvider(plan)
    ready = build(store, request, provider)
    assert ready["state"] == "ready_for_review", ready
    loaded, _ = check_candidate(store.read_artifact(ready["id"], "draft").decode())
    result = asyncio.run(replay_workflow(loaded, []))
    assert result.outputs == {"result": expected(base, (2,))[0]}


def test_running_stage_and_status_reads_do_not_repeat_inference(store, catalog_site):
    _, base = catalog_site

    async def run():
        provider = ControlledProvider(proposal(), gate=asyncio.Event())
        initial = store.start_task(str(uuid4()), task(base), LIMITS)
        store.claim()
        phase = asyncio.create_task(
            execute_phase(
                store,
                initial["id"],
                partial(generate_phase, provider=provider),
            )
        )
        for _ in range(200):
            if provider.calls:
                break
            await asyncio.sleep(0.02)
        assert provider.calls == 1
        current = store.read(initial["id"])
        assert current["stage"] == "ask_model"
        for _ in range(3):
            assert store.read(initial["id"]) == current
        provider.gate.set()
        await phase
        assert store.read(initial["id"])["state"] == "ready_for_review"
        assert provider.calls == 1

    asyncio.run(run())
