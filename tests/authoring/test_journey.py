"""Real capture -> guided authoring -> YAML -> Chromium -> separate STDIO server."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.command import Cancelled, author
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.recorder.draft import save_draft
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow


async def create_workflow(catalog, tmp_path, *, approval="save"):
    draft_path = tmp_path / "book.recording.json"
    async with RecordingSession(
        catalog + "/catalogue/a-light-in-the-attic_1000/index.html", ["127.0.0.1"], headless=True
    ) as recording:
        draft = await recording.finish()
    save_draft(draft, draft_path, tmp_path / "workflows")
    sessions = []

    def factory(*args, **kwargs):
        session = SelectionSession(*args, headless=True, **kwargs)
        sessions.append(session)
        return session

    answers = iter(
        [
            "yes",
            "yes",
            "catalog_product_lookup",
            "Look up a book",
            "yes",
            "product_slug",
            "string",
            "yes",
            "Book slug",
            "1",
            "0/path:2",
            "no",
            "no",
            "select:.product_main h1",
            "title",
            "string",
            "Book title",
            "yes",
            "select:.product_main .price_color",
            "price",
            "string",
            "Displayed price",
            "no",
            approval,
        ]
    )

    async def prompt(_message):
        answer = next(answers)
        if answer.startswith("select:"):
            await sessions[0].page.locator(answer.removeprefix("select:")).click()
            return ""
        return answer

    destination = tmp_path / "workflows/book.yaml"
    destination.parent.mkdir(exist_ok=True)
    try:
        await author(draft_path, destination, prompt=prompt, session_factory=factory)
    finally:
        assert all(session.browser is None for session in sessions)
    return destination


def test_cancelled_final_review_saves_nothing(catalog, tmp_path):
    with pytest.raises(Cancelled):
        asyncio.run(create_workflow(catalog, tmp_path, approval="no"))
    assert not list((tmp_path / "workflows").glob("*.yaml"))


def test_recording_to_real_mcp(catalog, tmp_path):
    async def exercise():
        path = await create_workflow(catalog, tmp_path)
        loaded = load_workflow(path)
        result = await replay_workflow(
            loaded, ["product_slug=shakespeares-sonnets_989"], tmp_path / "traces", 60
        )
        assert result.outputs == {"title": "Shakespeare's Sonnets", "price": "£20.66"}
        assert result.manifest_digest == loaded.manifest_digest
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "from browser_to_mcp.cli import main; main()",
                "server",
                "--workflow-dir",
                str(path.parent),
                "--trace-dir",
                str(tmp_path / "mcp-traces"),
            ],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                assert [tool.name for tool in tools] == ["catalog_product_lookup"]
                assert tools[0].inputSchema["required"] == ["product_slug"]
                for slug, title, price in [
                    ("a-light-in-the-attic_1000", "A Light in the Attic", "£51.77"),
                    ("shakespeares-sonnets_989", "Shakespeare's Sonnets", "£20.66"),
                    ("space & hash# café", "Special characters", "£12.34"),
                    ("delayed", "A Light in the Attic", "£51.77"),
                ]:
                    response = await client.call_tool(
                        "catalog_product_lookup", {"product_slug": slug}
                    )
                    assert not response.isError, response
                    data = response.structuredContent
                    assert data["workflow"] == loaded.workflow.name
                    assert data["manifest_digest"] == loaded.manifest_digest
                    assert data["outputs"] == {"title": title, "price": price}
                    assert all(type(value) is str for value in data["outputs"].values())
                    trace = json.loads(Path(data["trace_path"]).read_text())
                    assert trace["status"] == "completed"
                missing = await client.call_tool("catalog_product_lookup", {})
                assert missing.isError
                for slug in ["browser-to-mcp-missing_0", "broken"]:
                    response = await client.call_tool(
                        "catalog_product_lookup", {"product_slug": slug}
                    )
                    assert response.isError
        failures = [json.loads(p.read_text()) for p in (tmp_path / "mcp-traces").glob("*.json")]
        assert sum(t["status"] == "failed" for t in failures) >= 2

    asyncio.run(exercise())


def test_three_cases_ten_consecutive_runs(catalog, tmp_path):
    async def exercise():
        loaded = load_workflow(await create_workflow(catalog, tmp_path))
        for slug, expected in [
            ("a-light-in-the-attic_1000", {"title": "A Light in the Attic", "price": "£51.77"}),
            ("shakespeares-sonnets_989", {"title": "Shakespeare's Sonnets", "price": "£20.66"}),
            ("browser-to-mcp-missing_0", None),
        ]:
            for _ in range(10):
                if expected is None:
                    with pytest.raises(WorkflowExecutionError) as error:
                        await replay_workflow(
                            loaded, [f"product_slug={slug}"], tmp_path / "traces", 60
                        )
                    assert error.value.path == "steps.title"
                    trace = json.loads(Path(error.value.trace_path).read_text())
                    assert trace["status"] == "failed"
                else:
                    result = await replay_workflow(
                        loaded, [f"product_slug={slug}"], tmp_path / "traces", 60
                    )
                    assert result.outputs == expected

    asyncio.run(exercise())


def test_invalid_input_starts_no_browser(catalog, tmp_path, monkeypatch):
    from browser_to_mcp.replay import PlaywrightReplayEngine

    async def exercise():
        loaded = load_workflow(await create_workflow(catalog, tmp_path))

        async def forbidden(*_args, **_kwargs):
            pytest.fail("Invalid input started a replay browser")

        monkeypatch.setattr(PlaywrightReplayEngine, "_run", forbidden)
        with pytest.raises(WorkflowExecutionError) as error:
            await replay_workflow(loaded, [], tmp_path / "traces", 60)
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
