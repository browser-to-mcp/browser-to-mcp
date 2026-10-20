"""Real capture -> guided authoring -> YAML -> Chromium -> separate STDIO server."""

import asyncio
import json
from pathlib import Path

import pytest
from journey import create_workflow, inspect_trace, mcp_client

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.command import Cancelled
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow


def test_cancelled_final_review_saves_nothing(catalog, tmp_path):
    with pytest.raises(Cancelled):
        asyncio.run(create_workflow(catalog, tmp_path, approval="no"))
    assert not list((tmp_path / "workflows").glob("*.yaml"))


def test_recording_to_real_mcp(catalog, catalog_server, tmp_path):
    async def exercise():
        path = await create_workflow(catalog, tmp_path)
        loaded = load_workflow(path)
        trace_dir = tmp_path / "mcp-traces"
        async with mcp_client(path.parent, trace_dir) as client:
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == ["catalog_product_lookup"]
            assert tools[0].inputSchema["required"] == ["product_slug"]
            for slug, title, price in [
                ("a-light-in-the-attic_1000", "A Light in the Attic", "£51.77"),
                ("shakespeares-sonnets_989", "Shakespeare's Sonnets", "£20.66"),
                ("space & hash# café", "Special characters", "£12.34"),
            ]:
                response = await client.call_tool("catalog_product_lookup", {"product_slug": slug})
                assert not response.isError, response
                data = response.structuredContent
                expected = {"title": title, "price": price}
                assert data["workflow"] == loaded.workflow.name
                assert data["manifest_digest"] == loaded.manifest_digest
                assert data["outputs"] == expected
                assert all(type(value) is str for value in data["outputs"].values())
                inspect_trace(data["trace_path"], loaded, trace_dir, outputs=expected)
            requests_before = list(catalog_server.requests)
            traces_before = set(trace_dir.glob("*.json"))
            missing = await client.call_tool("catalog_product_lookup", {})
            assert missing.isError
            assert catalog_server.requests == requests_before
            # SDK schema rejection happens before replay, so no replay trace is created.
            assert set(trace_dir.glob("*.json")) == traces_before
            for mode in ("delayed", "broken", "challenge"):
                catalog_server.mode = mode
                response = await client.call_tool(
                    "catalog_product_lookup", {"product_slug": "a-light-in-the-attic_1000"}
                )
                data = response.structuredContent
                if mode == "delayed":
                    assert not response.isError
                    inspect_trace(
                        data["trace_path"],
                        loaded,
                        trace_dir,
                        outputs={"title": "A Light in the Attic", "price": "£51.77"},
                    )
                else:
                    assert response.isError
                    assert data["category"] == "runtime"
                    assert data["path"] == "steps.title"
                    assert "timed out" in data["message"]
                    trace = inspect_trace(
                        data["trace_path"],
                        loaded,
                        trace_dir,
                        category="runtime",
                        error_path="steps.title",
                    )
                    assert trace["actions"][-1]["status"] == "failed"
                    assert trace["actions"][-1]["step_id"] == "title"

    asyncio.run(exercise())


def test_three_cases_ten_consecutive_runs(catalog, tmp_path, journey_report):
    from time import monotonic

    async def exercise():
        path = await create_workflow(catalog, tmp_path)
        loaded = load_workflow(path)
        journey_report["manifest_digest"] = loaded.manifest_digest
        journey_report["workflow"] = loaded.workflow.model_dump(mode="json")
        trace_dir = tmp_path / "repeat-traces"
        async with mcp_client(path.parent, trace_dir) as client:
            for slug, expected in [
                ("a-light-in-the-attic_1000", {"title": "A Light in the Attic", "price": "£51.77"}),
                ("shakespeares-sonnets_989", {"title": "Shakespeare's Sonnets", "price": "£20.66"}),
                ("browser-to-mcp-missing_0", None),
            ]:
                for attempt in range(1, 11):
                    started = monotonic()
                    observation = {
                        "input": slug,
                        "attempt": attempt,
                        "matched": False,
                        "expected_outputs": expected,
                    }
                    journey_report["runs"].append(observation)
                    try:
                        response = await client.call_tool(
                            "catalog_product_lookup", {"product_slug": slug}
                        )
                        data = response.structuredContent
                        if expected is None:
                            assert response.isError
                            assert data["category"] == "runtime"
                            assert data["path"] == "steps.title"
                            trace = inspect_trace(
                                data["trace_path"],
                                loaded,
                                trace_dir,
                                category="runtime",
                                error_path="steps.title",
                            )
                            observation["error"] = {
                                key: data[key] for key in ("category", "path", "message")
                            }
                        else:
                            assert not response.isError, response
                            assert data["outputs"] == expected
                            assert data["manifest_digest"] == loaded.manifest_digest
                            assert data["workflow"] == loaded.workflow.name
                            trace = inspect_trace(
                                data["trace_path"], loaded, trace_dir, outputs=expected
                            )
                            observation["outputs"] = data["outputs"]
                        observation.update(
                            matched=True,
                            trace_id=trace["metadata"]["run_id"],
                            status=trace["status"],
                        )
                        journey_report["runtime"] = {
                            key: trace["metadata"][key]
                            for key in (
                                "python_version",
                                "playwright_version",
                                "browser_version",
                                "platform",
                            )
                        }
                    finally:
                        observation["duration_seconds"] = round(monotonic() - started, 3)
        journey_report["completed"] = True

    asyncio.run(exercise())


@pytest.mark.parametrize("inputs", [[], ["product_slug="]])
def test_invalid_input_starts_no_browser(catalog, tmp_path, monkeypatch, inputs):
    from browser_to_mcp.replay import PlaywrightReplayEngine

    async def exercise():
        loaded = load_workflow(await create_workflow(catalog, tmp_path))

        async def forbidden(*_args, **_kwargs):
            pytest.fail("Invalid input started a replay browser")

        monkeypatch.setattr(PlaywrightReplayEngine, "_run", forbidden)
        with pytest.raises(WorkflowExecutionError) as error:
            await replay_workflow(loaded, inputs, tmp_path / "traces", 60)
        assert error.value.category.value == "input"
        trace = json.loads(Path(error.value.trace_path).read_text())
        assert trace["status"] == "failed"
        assert trace["actions"] == []

    asyncio.run(exercise())


def test_selection_rejects_ambiguous_and_hidden_targets(catalog):
    from browser_to_mcp.authoring.plan import AuthoringError, step_fields

    async def exercise():
        url = catalog + "/catalogue/a-light-in-the-attic_1000/index.html"
        async with SelectionSession(url, ["127.0.0.1"], headless=True) as session:
            await session.prepare([step_fields("goto", "start", url=url)])
            await session.page.locator(".product_main h1").click()
            target, preview = await session.selected()
            assert preview == "A Light in the Attic"
            await session.page.evaluate("""() => {
                const main = document.querySelector('main');
                document.body.append(main.cloneNode(true));
            }""")
            with pytest.raises(AuthoringError, match="ambiguous"):
                await session.selected()
            await session.page.evaluate("document.querySelectorAll('main')[1].remove()")
            await session.page.locator(target.selector).evaluate("el => el.hidden = true")
            with pytest.raises(AuthoringError, match="hidden"):
                await session.selected()

    asyncio.run(exercise())


def test_link_capture_replays_one_click_without_duplicate_navigation(catalog, tmp_path):
    async def exercise():
        path = await create_workflow(catalog, tmp_path, via_link=True)
        loaded = load_workflow(path)
        assert [step.action.value for step in loaded.workflow.steps] == [
            "goto",
            "click",
            "extract_text",
            "extract_text",
        ]
        assert loaded.workflow.inputs == {}
        result = await replay_workflow(loaded, [], tmp_path / "traces", 60)
        assert result.outputs == {"title": "Shakespeare's Sonnets", "price": "£20.66"}
        inspect_trace(result.trace_path, loaded, tmp_path / "traces", outputs=result.outputs)

    asyncio.run(exercise())


def test_authored_primitive_outputs_match_cli_mcp_and_trace(catalog, catalog_server, tmp_path):
    import subprocess
    import sys

    async def exercise():
        path = await create_workflow(catalog, tmp_path, typed=True)
        loaded = load_workflow(path)
        validate = subprocess.run(
            [
                sys.executable,
                "-c",
                "from browser_to_mcp.cli import main; main()",
                "validate",
                str(path),
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert validate.returncode == 0, validate.stderr
        assert json.loads(validate.stdout)["manifest_digest"] == loaded.manifest_digest
        trace_dir = tmp_path / "typed-traces"
        async with mcp_client(path.parent, trace_dir) as client:
            for mode in ("normal", "bad_count"):
                catalog_server.mode = mode
                cli = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from browser_to_mcp.cli import main; main()",
                        "replay",
                        str(path),
                        "--input",
                        "product_slug=shakespeares-sonnets_989",
                        "--trace-dir",
                        str(trace_dir),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=90,
                )
                mcp = await client.call_tool(
                    "catalog_product_lookup", {"product_slug": "shakespeares-sonnets_989"}
                )
                if mode == "normal":
                    assert cli.returncode == 0, cli.stderr
                    assert not mcp.isError
                    expected = {
                        "title": "Shakespeare's Sonnets",
                        "price": "£20.66",
                        "count": 0,
                        "available": False,
                        "amount": 0.0,
                    }
                    for result in (json.loads(cli.stdout), mcp.structuredContent):
                        assert result["outputs"] == expected
                        for key, value in expected.items():
                            assert type(result["outputs"][key]) is type(value)
                        inspect_trace(result["trace_path"], loaded, trace_dir, outputs=expected)
                else:
                    assert cli.returncode == 1
                    assert mcp.isError
                    for result in (json.loads(cli.stderr), mcp.structuredContent):
                        assert result["category"] == "assertion"
                        assert result["path"] == "outputs.count"
                        inspect_trace(
                            result["trace_path"],
                            loaded,
                            trace_dir,
                            category="assertion",
                            error_path="outputs.count",
                        )

    asyncio.run(exercise())


@pytest.mark.parametrize("retry", ["one", "bounded"])
def test_authored_workflow_retry_edit_refused_at_cli_and_server(
    catalog, catalog_server, tmp_path, retry
):
    import subprocess
    import sys

    import yaml

    path = asyncio.run(create_workflow(catalog, tmp_path))
    raw = yaml.safe_load(path.read_text())
    raw["steps"][0]["retry"] = retry
    path.write_text(yaml.safe_dump(raw))
    requests_before = list(catalog_server.requests)
    for args in (["validate", str(path)], ["server", "--workflow-dir", str(path.parent)]):
        result = subprocess.run(
            [sys.executable, "-c", "from browser_to_mcp.cli import main; main()", *args],
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 2
        assert "retry" in result.stderr and "none" in result.stderr
        assert result.stdout == ""
    assert catalog_server.requests == requests_before
