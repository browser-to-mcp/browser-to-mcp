import copy
import json
from uuid import uuid4

import pytest
from builder_site import proposal, task

from browser_to_mcp.builder.contracts import check_task, make_candidate
from browser_to_mcp.builder.service import safe_observation
from browser_to_mcp.jobs.models import Budgets, JobError
from browser_to_mcp.replay import render_url_template


def candidate(request=None, plan=None):
    return make_candidate(
        check_task(request or task("http://127.0.0.1:1234"))[0],
        json.dumps(plan or proposal()),
    )


def test_user_owns_contract_and_examples_become_inputs():
    loaded, data = candidate()
    workflow = loaded.workflow
    assert workflow.permissions.allowed_domains == ["127.0.0.1"]
    assert not workflow.permissions.downloads
    assert workflow.inputs["query"].required
    assert workflow.steps[0].url == "http://127.0.0.1:1234/books/?q={{query}}"
    assert render_url_template(workflow.steps[0].url, {"query": "x&new=1"}).endswith(
        "q=x%26new%3D1"
    )
    assert b"notebook" not in data
    assert workflow.steps[-1].paging.max_pages == 2
    assert workflow.outputs["result"].max_items == 10
    assert set(workflow.outputs["result"].record_fields) == {"title", "price_text", "product_url"}
    assert all(step.risk == "read_only" and step.retry == "none" for step in workflow.steps)


def test_path_inputs_and_optional_examples():
    request = task("http://127.0.0.1:1234", max_pages=1)
    request["start_url"] = "http://127.0.0.1:1234/catalog/red%20notebook?fixed=1"
    request["inputs"]["query"]["example"] = "red notebook"
    plan = proposal(paged=False)
    plan["bindings"]["query"] = dict(location="path", key="2")
    loaded, _ = candidate(request, plan)
    assert loaded.workflow.steps[0].url.endswith("/catalog/{{query}}?fixed=1")
    del request["inputs"]["query"]["example"]
    assert candidate(request, plan)[0].manifest_digest == loaded.manifest_digest


@pytest.mark.parametrize(
    "change",
    [
        {"allowed_domains": ["example.com"]},
        {"start_url": "javascript:alert(1)"},
        {"start_url": "http://user:password@127.0.0.1/"},
        {"start_url": "http://127.0.0.1/?token=private"},
        {"start_url": "http://127.0.0.1/{{query}}"},
        {"description": " "},
        {"max_pages": True},
        {"max_pages_input": "query"},
        {"max_pages_input": "api_key"},
        {"max_pages_input": "max_pages", "max_pages": 1},
        {"max_items": 101},
        {"allow_empty": "false"},
        {"shape": "object"},
        {"inputs": {"api_key": {"description": "Private"}}},
        {"fields": {"x": {"description": "Link", "link_path_prefix": "//evil/"}}},
        {"fields": {}},
        {"description": "password=private"},
        {"inputs": {"query": {"type": "integer", "description": "Query", "example": True}}},
        {"inputs": {"query": {"type": "number", "description": "Query", "example": float("nan")}}},
        {"publish": True},
    ],
)
def test_invalid_tasks_fail_before_storage(store, change):
    with pytest.raises(JobError) as raised:
        store.start_task(str(uuid4()), task("http://127.0.0.1:1234") | change, Budgets())
    assert raised.value.code == "invalid_task"
    assert not store.directory.exists()
    assert "private" not in raised.value.message


@pytest.mark.parametrize(
    "change",
    [
        {"permissions": {"allowed_domains": ["evil.example"]}},
        {"python": "print('bad')"},
        {"javascript": "fetch('https://evil.example')"},
        {"shell": "touch /tmp/bad"},
        {"steps": [{"action": "click", "selector": ".cart"}]},
        {"start_url": "https://evil.example"},
        {"publish": True},
        {"bindings": {}},
        {"bindings": {"query": {"location": "query", "key": "missing"}}},
        {"bindings": {"query": {"location": "path", "key": "0"}}},
        {"bindings": {"query": {"location": "query", "key": "page"}}},
        {"paging": None},
        {"reason": "ambiguous"},
    ],
)
def test_model_cannot_change_capabilities_or_drop_inputs(change):
    with pytest.raises(JobError) as raised:
        candidate(plan=proposal() | change)
    assert raised.value.code in {"invalid_proposal", "invalid_candidate"}


@pytest.mark.parametrize("kind", ["fields", "links", "duplicates", "empty", "example"])
def test_model_must_obey_exact_output_and_binding_rules(kind):
    request, plan = task("http://127.0.0.1:1234"), proposal()
    if kind == "fields":
        del plan["record"]["fields"]["price_text"]
    elif kind == "links":
        plan["record"]["fields"]["product_url"]["path_prefix"] = "/"
    elif kind == "duplicates":
        plan["record"]["duplicates"] = "keep"
    elif kind == "empty":
        plan["record"]["empty_selector"] = None
    else:
        request["inputs"]["query"]["example"] = "different"
    with pytest.raises(JobError):
        candidate(request, plan)


@pytest.mark.parametrize(
    "reason", ["login", "challenge", "interaction", "ambiguous", "missing_data"]
)
def test_unsupported_reasons_have_safe_actionable_messages(reason):
    with pytest.raises(JobError) as raised:
        candidate(plan={"status": "unsupported", "reason": reason})
    assert raised.value.code == "task_" + reason


def test_invalid_and_oversized_model_responses_are_refused():
    request, _ = check_task(task("http://127.0.0.1:1234"))
    for value in ["```json\n{}\n```", "null", "[]", "x" * 65537]:
        with pytest.raises(JobError):
            make_candidate(request, value)


def test_task_jobs_are_idempotent_and_keep_task_data_out_of_metadata(store):
    request = task("http://127.0.0.1:1234")
    key = str(uuid4())
    initial = store.start_task(key, request, Budgets())
    reordered = dict(reversed(list(copy.deepcopy(request).items())))
    assert store.start_task(key, reordered, Budgets()) == initial
    assert initial["kind"] == "generate_workflow"
    assert "notebook" not in json.dumps(initial)
    assert "127.0.0.1" not in json.dumps(initial)
    assert store.read_artifact(initial["id"], "input")
    with pytest.raises(JobError, match="different work"):
        store.start_task(key, request | {"name": "other"}, Budgets())


def test_page_view_omits_malformed_or_unsafe_links_without_losing_safe_relative_links():
    value = safe_observation(
        {
            "nodes": [
                {"attrs": {"href": "http://[invalid"}},
                {"attrs": {"href": "javascript:alert(1)"}},
                {"attrs": {"href": "/?token=PRIVATE"}},
                {"attrs": {"href": "/products/blue"}},
                {"text": "Bearer PRIVATE"},
            ]
        },
        "http://127.0.0.1:1234/books/",
    )
    assert [node["attrs"]["href"] for node in value["nodes"][:4]] == [
        "[omitted]",
        "[omitted]",
        "[omitted]",
        "/products/blue",
    ]
    assert value["nodes"][-1]["text"] == "[omitted]"
    assert "PRIVATE" not in json.dumps(value)
