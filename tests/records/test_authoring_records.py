"""Actual clicks and terminal answers create reviewed row tools without typing selectors."""

import asyncio

import pytest
from record_site import expected

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.command import Cancelled, author
from browser_to_mcp.authoring.plan import AuthoringError, step_fields
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.recorder.draft import save_draft
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.replay import replay_workflow


async def selected_index(session, mode, node_id=None, row=False):
    return str(
        await session.page.evaluate(
            """({mode,nodeId,row}) => {
        const candidates = mode === 'area' ? window.__btmRecordAreas : window.__btmRecordRows;
        return candidates.findIndex(c => row ? c.node.matches('article.product')
            : c.node.id===nodeId) + 1;
    }""",
            {"mode": mode, "nodeId": node_id, "row": row},
        )
    )


async def create(
    records_site,
    tmp_path,
    *,
    allow_empty=False,
    approval="save",
    kind="array",
    initial_case=None,
    cancel_on_retry=False,
    href_prefix="/products/",
):
    case = initial_case or ("single" if kind == "object" else "normal")
    url = records_site.base + "/catalog?case=" + case
    draft_path = tmp_path / "catalog.recording.json"
    async with RecordingSession(url, ["127.0.0.1"], headless=True) as recording:
        draft = await recording.finish()
    assert draft.status == "captured"
    destination = tmp_path / "workflows/rows.yaml"
    destination.parent.mkdir()
    save_draft(draft, draft_path, destination.parent)
    sessions = []

    def factory(*args, **kwargs):
        session = SelectionSession(*args, headless=True, **kwargs)
        sessions.append(session)
        return session

    answers = [
        "yes",
        "yes",
        "catalog_rows",
        "Read product rows",
        "yes",
        "case",
        "string",
        "yes",
        "Catalog example",
        "1",
        "0/query:0",
        "no",
        "no",
        "pick:#results h2",
        "products",
        kind,
        "Product rows",
        "area",
        "pick:article.product:first-of-type a.title",
        "row",
    ]
    if kind == "array":
        answers += ["10"]
    fields = [
        ("title", "string", "title", "a.title", False, False),
        ("price_text", "string", "text", ".price", False, False),
        ("product_url", "string", "href", "a.title", False, False),
        ("count", "integer", "text", ".count", False, False),
        ("amount", "number", "text", ".amount", False, False),
        ("available", "boolean", "text", ".available", False, False),
        ("note", "string", "text", ".note", True, True),
    ]
    for index, (name, field_kind, source, selector, nullable, sensitive) in enumerate(fields):
        answers += [name, field_kind, source]
        if source == "href":
            answers += [href_prefix]
        answers += [
            "yes" if nullable else "no",
            "yes" if sensitive else "no",
            "pick:article.product:first-of-type " + selector,
            "yes" if index < len(fields) - 1 else "no",
        ]
    answers += ["deduplicate", "product_url"]
    if kind == "array":
        answers += ["yes" if allow_empty else "no"]
    if allow_empty:
        answers += ["yes", "navigate-empty", "pick:.empty"]
    answers += ["yes", "no", approval]
    answers = iter(answers)

    selections = 0

    async def prompt(message):
        nonlocal selections
        if message.startswith("Click visible text to return"):
            selections += 1
            if cancel_on_retry and selections > 1:
                raise Cancelled
        answer = next(answers)
        if answer == "area":
            return await selected_index(sessions[0], "area", node_id="results")
        if answer == "row":
            return await selected_index(sessions[0], "row", row=True)
        if answer == "navigate-empty":
            await sessions[0].page.goto(records_site.base + "/catalog?case=empty")
            return ""
        if answer.startswith("pick:"):
            await sessions[0].page.locator(answer.removeprefix("pick:")).click()
            return ""
        return answer

    try:
        await author(draft_path, destination, prompt=prompt, session_factory=factory)
    finally:
        assert all(s.browser is None and s.context is None for s in sessions)
    with pytest.raises(StopIteration):
        next(answers)
    return destination


@pytest.mark.parametrize("allow_empty", [False, True])
def test_guided_row_tool_has_typed_preview_and_runs_with_new_input(
    records_site, tmp_path, capsys, allow_empty
):
    path = asyncio.run(create(records_site, tmp_path, allow_empty=allow_empty))
    printed = capsys.readouterr()
    assert "PRIVATE-NOTE" not in printed.err
    assert "Record preview:" in printed.err and "[REDACTED]" in printed.err
    loaded = load_workflow(path)
    assert loaded.workflow.steps[-1].action == "extract_records"
    assert loaded.workflow.outputs["products"].allow_empty == allow_empty
    result = asyncio.run(replay_workflow(loaded, ["case=order"], tmp_path / "traces"))
    assert result.outputs == {"products": expected(records_site.base, (1, 2, 0))}
    if allow_empty:
        empty = asyncio.run(replay_workflow(loaded, ["case=empty"]))
        assert empty.outputs == {"products": []}
        assert "Populated preview:" in printed.err
    assert records_site.hits["/cart"] == 0
    assert not any(p.startswith("/products/") for p in records_site.hits)


def test_cancelling_final_record_review_never_exports(records_site, tmp_path):
    with pytest.raises(Cancelled):
        asyncio.run(create(records_site, tmp_path, approval="cancel"))
    assert not (tmp_path / "workflows/rows.yaml").exists()
    assert not list((tmp_path / "workflows").glob("*.tmp"))


def test_field_cannot_be_selected_from_a_different_row_or_sidebar(records_site):
    async def exercise():
        url = records_site.base + "/catalog?case=normal"
        async with SelectionSession(url, ["127.0.0.1"], headless=True) as session:
            await session.prepare([step_fields("goto", "open", url=url)])
            await session.page.locator("#results h2").click()
            await session.inspect_record("areas")
            index = int(await selected_index(session, "area", node_id="results")) - 1
            await session.inspect_record("area", index=index)
            await session.page.locator("article.product").first.locator("a.title").click()
            await session.inspect_record("rows", minimum=2)
            index = int(await selected_index(session, "row", row=True)) - 1
            await session.inspect_record("row", index=index)
            for selector in ["article.product:nth-of-type(2) .price", "#outside-title"]:
                await session.page.locator(selector).click()
                with pytest.raises(AuthoringError, match="same example row"):
                    await session.inspect_record("field", source="text")

    asyncio.run(exercise())


def test_guided_single_record(records_site, tmp_path):
    path = asyncio.run(create(records_site, tmp_path, kind="object"))
    loaded = load_workflow(path)
    assert loaded.workflow.outputs["products"].type == "object"
    result = asyncio.run(replay_workflow(loaded, ["case=single"]))
    assert result.outputs == {"products": expected(records_site.base)[0]}


@pytest.mark.parametrize("case", ["bad_number", "ambiguous", "credential"])
def test_invalid_or_credential_preview_cannot_be_saved(records_site, tmp_path, capsys, case):
    with pytest.raises(Cancelled):
        asyncio.run(create(records_site, tmp_path, initial_case=case, cancel_on_retry=True))
    assert not (tmp_path / "workflows/rows.yaml").exists()
    printed = capsys.readouterr().err
    assert "PRIVATE-NOTE" not in printed and "NEVER-PRINT-THIS" not in printed
    assert "Record preview:" not in printed


def test_guided_selection_handles_different_row_and_field_classes(records_site, tmp_path):
    path = asyncio.run(create(records_site, tmp_path, initial_case="varying_classes"))
    loaded = load_workflow(path)
    result = asyncio.run(replay_workflow(loaded, ["case=order"]))
    assert result.outputs == {"products": expected(records_site.base, (1, 2, 0))}


def test_invalid_link_rule_never_echoes_private_parameters(records_site, tmp_path, capsys):
    with pytest.raises(Cancelled):
        asyncio.run(
            create(
                records_site,
                tmp_path,
                cancel_on_retry=True,
                href_prefix="/products/?token=NEVER-PRINT-THIS",
            )
        )
    assert not (tmp_path / "workflows/rows.yaml").exists()
    assert "NEVER-PRINT-THIS" not in capsys.readouterr().err
