from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from browser_to_mcp.loader import load_workflow, manifest_digest
from browser_to_mcp.validation import FailureCategory, WorkflowValidationError

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


@pytest.mark.parametrize(
    "url",
    [
        "{{query}}://hn.algolia.com/",
        "ht{{query}}ps://hn.algolia.com/",
        "https://{{query}}.hn.algolia.com/",
        "https://hn.algolia.com:{{query}}/",
        "https://{{query}}@hn.algolia.com/",
        "https://hn.algolia.com/?{{query}}=fixed",
        "https://hn.algolia.com/?q=fixed&{{query}}",
        "https://hn.algolia.com/#{{query}}",
        "https://hn.algolia.com/prefix-{{query}}",
        "https://hn.algolia.com/{{query}}.html",
        "https://hn.algolia.com/?q={{query | urlencode}}",
        "https://hn.algolia.com/?q={{query",
        "https://hn.algolia.com/?q=%{{query}}",
        "https://hn.algolia.com/?q=%2{{query}}",
        "https://hn.algolia.com:invalid/",
        "https://[{{query}}]/",
        "ftp://hn.algolia.com/?q={{query}}",
        "https://hn.algolia.com\\@evil.example/?q={{query}}",
    ],
)
def test_rejects_unsupported_url_templates(tmp_path: Path, url: str) -> None:
    invalid = tmp_path / "url.yaml"
    contract = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    contract["steps"][0]["url"] = url
    invalid.write_text(yaml.safe_dump(contract), encoding="utf-8")

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA
    assert error.value.path == "steps.search.url"


@pytest.mark.parametrize(
    "url",
    [
        "https://hn.algolia.com/?q={{ query }}&fixed=one%2Btwo#results",
        "https://hn.algolia.com/items/{{query}}/details?lang=en#details",
        "https://hn.algolia.com/?q=prefix-{{query}}&q={{query}}",
    ],
)
def test_accepts_supported_url_templates(tmp_path: Path, url: str) -> None:
    valid = tmp_path / "url.yaml"
    valid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            "https://hn.algolia.com/?q={{query}}", url
        ),
        encoding="utf-8",
    )

    assert load_workflow(valid).workflow.steps[0].url == url


def test_loads_reference_workflow() -> None:
    loaded = load_workflow(WORKFLOW_PATH)

    assert loaded.workflow.name == "hn_search"
    assert loaded.manifest_digest.startswith("sha256:")


@pytest.mark.parametrize("action", [None, "goto", "fill", "click", "wait_for_text", "press"])
def test_outputs_require_supported_producers(tmp_path: Path, action: str | None) -> None:
    contract = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if action is None:
        contract["steps"].pop()
    else:
        contract["steps"][-1].update(
            action=action,
            url="https://hn.algolia.com/",
            value="text",
            key="Enter",
            text="Ready",
        )
    path = tmp_path / "producer.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(path)

    assert error.value.category is FailureCategory.SCHEMA
    assert error.value.path == "outputs.results"
    assert "same ID" in error.value.message


@pytest.mark.parametrize("name", ["not-valid", "class", "model_dump", "_private"])
def test_output_names_must_be_supported_by_mcp(tmp_path: Path, name: str) -> None:
    contract = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    contract["outputs"][name] = contract["outputs"].pop("results")
    contract["steps"][-1]["id"] = name
    contract["success"][0]["field"] = name
    path = tmp_path / "name.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(path)

    assert error.value.category is FailureCategory.SCHEMA
    assert error.value.path == f"outputs.{name}"


@pytest.mark.parametrize("type_name", ["string", "integer", "number", "boolean"])
def test_download_produces_only_a_string_filename(tmp_path: Path, type_name: str) -> None:
    contract = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    contract["permissions"]["downloads"] = True
    contract["steps"][-1]["action"] = "download"
    contract["outputs"]["results"]["type"] = type_name
    path = tmp_path / "download.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    if type_name == "string":
        assert load_workflow(path).workflow.outputs["results"].type == "string"
    else:
        with pytest.raises(WorkflowValidationError) as error:
            load_workflow(path)
        assert error.value.category is FailureCategory.SCHEMA
        assert error.value.path == "outputs.results"


def test_manifest_digest_is_stable() -> None:
    first = load_workflow(WORKFLOW_PATH).workflow
    second = load_workflow(WORKFLOW_PATH).workflow

    assert manifest_digest(first) == manifest_digest(second)


def test_manifest_digest_changes_when_reviewed_definition_changes(tmp_path: Path) -> None:
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            "Search Hacker News and return visible matching results.",
            "Search Hacker News with a revised reviewed description.",
        ),
        encoding="utf-8",
    )

    assert load_workflow(changed).manifest_digest != load_workflow(WORKFLOW_PATH).manifest_digest


def test_rejects_non_read_only_workflow(tmp_path: Path) -> None:
    invalid = tmp_path / "write.yaml"
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    invalid.write_text(
        source.replace("risk: read_only", "risk: financial"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.POLICY


def test_rejects_unauthorized_domain(tmp_path: Path) -> None:
    invalid = tmp_path / "domain.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("hn.algolia.com/?q", "example.com/?q"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.PERMISSION


def test_rejects_assertion_for_undeclared_output(tmp_path: Path) -> None:
    invalid = tmp_path / "assertion.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("field: results", "field: missing"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA


def test_rejects_missing_action_fields(tmp_path: Path) -> None:
    invalid = tmp_path / "step.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("    selector: main\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA
    assert "steps.2" in error.value.path


def test_rejects_non_string_min_length(tmp_path: Path) -> None:
    invalid = tmp_path / "input.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("type: string", "type: integer"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA


@pytest.mark.parametrize("retry", ["none", "one", "bounded"])
@pytest.mark.parametrize("idempotency", ["safe", "retryable", "non_retryable"])
def test_retry_policy_and_contradiction_precedence(
    tmp_path: Path, retry: str, idempotency: str
) -> None:
    contract = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # Use a later step to prove the rule applies beyond the first navigation.
    contract["steps"][2].update(retry=retry, idempotency=idempotency)
    path = tmp_path / "retry.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    if retry == "none":
        assert load_workflow(path).workflow.steps[2].retry == retry
        return

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(path)

    assert error.value.category is FailureCategory.POLICY
    assert error.value.path == "steps.results.retry"
    assert error.value.message == (
        "A non_retryable step must declare retry: none."
        if idempotency == "non_retryable"
        else "V0 does not support retries. Set retry to none."
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "    timeout_seconds: 30\n",
        "    risk: read_only\n",
        "    idempotency: safe\n",
        "    retry: none\n",
        "    confirmation: never\n",
    ],
)
def test_rejects_missing_explicit_safety_declaration(tmp_path: Path, declaration: str) -> None:
    invalid = tmp_path / "missing-declaration.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(declaration, "", 1),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA
    assert ":steps." in error.value.path


def test_rejects_confirmation_required_by_unsupported_v0_policy(tmp_path: Path) -> None:
    invalid = tmp_path / "confirmation.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            "    confirmation: never", "    confirmation: before_action", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.POLICY
    assert error.value.path == "steps.search.confirmation"


@pytest.mark.parametrize("capability", ["uploads", "submit_forms", "send_messages", "spend_money"])
def test_rejects_unsupported_side_effect_capability(tmp_path: Path, capability: str) -> None:
    invalid = tmp_path / f"{capability}.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            f"  {capability}: false", f"  {capability}: true", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.PERMISSION
    assert error.value.path == f"permissions.{capability}"


def test_rejects_authentication_sensitive_workflow(tmp_path: Path) -> None:
    invalid = tmp_path / "authentication.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            "  spend_money: false", "  spend_money: false\n  requires_authentication: true", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.PERMISSION
    assert error.value.path == "permissions.requires_authentication"


def test_rejects_undeclared_contract_fields(tmp_path: Path) -> None:
    invalid = tmp_path / "extra.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8") + "\nundocumented_setting: true\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA
