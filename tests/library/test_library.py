import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from evaluation_site import candidate, serve, suite, write_candidate

from browser_to_mcp.cli import run
from browser_to_mcp.evaluation.service import digest, evaluate
from browser_to_mcp.library.store import Library, LibraryError
from browser_to_mcp.server import create_server


@pytest.fixture(scope="module")
def tested(tmp_path_factory):
    root = tmp_path_factory.mktemp("library-evidence")
    with serve() as site:
        path = write_candidate(root / "draft.candidate", candidate(site.base))
        plan = root / "plan.json"
        plan.write_text(json.dumps(suite(site.base)))
        pin = digest(plan.read_bytes())
        evidence = root / "attempt"
        assert asyncio.run(evaluate(path, plan, evidence, pin)).eligible
        yield path, evidence, pin, site


def imported(tmp_path, tested):
    library = Library(tmp_path / "library")
    review = library.import_draft(tested[0], origin="controlled test job")
    tool = review["tool"]["id"]
    return library, tool, review


def attach_version(library, tool, version, tested):
    return library.attach_tests(tool, version, tested[1], tested[2])


def activate(library, review, **kwargs):
    return library.activate(
        review["tool"]["id"],
        review["version"]["version"],
        revision=review["tool"]["revision"],
        candidate_sha256=review["version"]["sha"],
        **kwargs,
    )


def test_lifecycle_pinning_rollback_retire_and_evidence(tmp_path, tested):
    library, tool, first = imported(tmp_path, tested)
    assert first["version"]["state"] == "draft"
    assert library.published() == ()
    first = attach_version(library, tool, 1, tested)
    assert first["version"]["state"] == "tested"
    with pytest.raises(LibraryError, match="Explicit approval"):
        activate(library, first)
    first = activate(library, first, approve=True)
    pinned = library.published()
    assert pinned[0].publication.version == 1
    # Importing the exact same source still creates a distinct immutable library version.
    second = library.import_draft(tested[0], origin="second job", tool_id=tool)
    assert second["version"]["version"] == 2
    second = attach_version(library, tool, 2, tested)
    second = activate(library, second, approve=True)
    assert library.published()[0].publication.version == 2
    assert pinned[0].publication.version == 1
    first = library.review(tool, 1)
    activate(library, first, rollback=True)
    assert library.published()[0].publication.version == 1
    library.retire(tool, revision=library.review(tool, 1)["tool"]["revision"])
    assert library.published() == ()
    assert library.review(tool, 1)["version"]["state"] == "retired"
    assert library.review(tool, 1)["evidence"]
    activate(library, library.review(tool, 2), rollback=True)
    assert library.published()[0].publication.version == 2
    assert [a["action"] for a in library.history(tool)] == [
        "import",
        "tested",
        "publish",
        "import",
        "tested",
        "publish",
        "rollback",
        "retire",
        "rollback",
    ]


def test_duplicate_name_and_unknown_tool_are_atomic(tmp_path, tested):
    library, tool, first = imported(tmp_path, tested)
    with pytest.raises(LibraryError, match="name already exists"):
        library.import_draft(tested[0], origin="another job")
    assert len(library.list()) == 1
    assert library.history(tool)[-1]["revision"] == 1
    with pytest.raises(LibraryError, match="Unknown tool ID"):
        library.review("missing", 1)


def test_edit_requires_tests_and_review_and_stale_approval_fails(tmp_path, tested):
    library, tool, first = imported(tmp_path, tested)
    first = attach_version(library, tool, 1, tested)
    library.import_draft(tested[0], tool_id=tool, origin="changed draft")
    with pytest.raises(LibraryError, match="stale"):
        activate(library, first, approve=True)
    with pytest.raises(LibraryError, match="newer draft"):
        activate(library, library.review(tool, 1), approve=True)
    with pytest.raises(LibraryError, match="passing evidence"):
        activate(library, library.review(tool, 2), approve=True)
    assert not library.published()


def test_changed_candidate_cannot_reuse_evidence(tmp_path, tested):
    library, tool, first = imported(tmp_path, tested)
    source = tmp_path / "changed.candidate"
    source.write_text(tested[0].read_text().replace("description:", "description: Changed ", 1))
    library.import_draft(source, origin="changed", tool_id=tool)
    failed = attach_version(library, tool, 2, tested)
    assert failed["version"]["state"] == "failed"
    with pytest.raises(LibraryError, match="passing evidence"):
        activate(library, failed, approve=True)
    assert not library.published()


def test_concurrent_approvals_have_one_winner(tmp_path, tested):
    library, tool, _ = imported(tmp_path, tested)
    review = attach_version(library, tool, 1, tested)

    def publish():
        try:
            activate(library, review, approve=True)
            return "published"
        except LibraryError:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: publish(), range(2))) == ["conflict", "published"]
    assert len([e for e in library.history(tool) if e["action"] == "publish"]) == 1


def test_saved_evidence_is_independent_and_tampering_fails_closed(tmp_path, tested):
    library, tool, _ = imported(tmp_path, tested)
    review = attach_version(library, tool, 1, tested)
    activate(library, review, approve=True)
    with library.connection(write=True) as db:
        db.execute("UPDATE evidence SET files='{}' WHERE tool=?", (tool,))
    with pytest.raises(LibraryError, match="changed"):
        library.published()


def test_cli_import_review_and_rejected_publish_create_no_callable_tool(tmp_path, tested, capsys):
    args = ["library", "--directory", str(tmp_path / "library")]
    assert run(args + ["import", str(tested[0]), "--origin", "explicit YAML migration"]) == 0
    review = json.loads(capsys.readouterr().out)
    tool = review["tool"]["id"]
    assert (
        run(
            args
            + [
                "publish",
                tool,
                "1",
                "--revision",
                "1",
                "--candidate-sha256",
                review["version"]["sha"],
                "--approve",
            ]
        )
        == 2
    )
    assert "passing evidence" in capsys.readouterr().err
    assert Library(tmp_path / "library").published() == ()


def test_real_browser_mcp_and_trace_identify_pinned_version(tmp_path, tested):
    library, tool, _ = imported(tmp_path, tested)
    review = attach_version(library, tool, 1, tested)
    activate(library, review, approve=True)
    snapshot = library.published()
    server = create_server(snapshot, tmp_path / "traces", timeout_seconds=15)
    library.retire(tool, revision=library.review(tool, 1)["tool"]["revision"])

    async def invoke():
        tools = await server.list_tools()
        assert len(tools) == 1
        return await server.call_tool(tools[0].name, {"query": "red", "color": "all"})

    result = asyncio.run(invoke())
    # FastMCP's direct API returns content plus structured data; trace is authoritative.
    traces = list((tmp_path / "traces").glob("*.json"))
    assert len(traces) == 1, result
    trace = json.loads(traces[0].read_text())
    assert trace["status"] == "completed"
    assert trace["metadata"]["publication"] == {
        "tool_id": tool,
        "version": 1,
        "candidate_sha256": review["version"]["sha"],
    }
    assert trace["outputs"]["products"][0]["title"] == "Red Notebook"
    assert library.published() == ()


def test_real_stdio_server_adopts_changes_only_on_restart(tmp_path, tested):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    library, tool, _ = imported(tmp_path, tested)
    review = attach_version(library, tool, 1, tested)
    activate(library, review, approve=True)
    parameters = StdioServerParameters(
        command=str(Path(__file__).parents[2] / ".venv/bin/browser-to-mcp"),
        args=[
            "library",
            "--directory",
            str(library.directory),
            "server",
            "--trace-dir",
            str(tmp_path / "traces"),
        ],
    )

    async def invoke():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert len(tools.tools) == 1
                library.retire(tool, revision=library.review(tool, 1)["tool"]["revision"])
                result = await client.call_tool(
                    tools.tools[0].name, {"query": "red", "color": "all"}
                )
                assert not result.isError
                assert result.meta["publication"]["tool_id"] == tool
                assert result.meta["publication"]["version"] == 1
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert (await client.list_tools()).tools == []

    asyncio.run(invoke())


@pytest.mark.parametrize("change", ["hash", "rollback", "approval", "missing_evidence"])
def test_invalid_publication_leaves_no_approval_or_live_pointer(tmp_path, tested, change):
    library, tool, _ = imported(tmp_path, tested)
    review = (
        library.review(tool, 1)
        if change == "missing_evidence"
        else attach_version(library, tool, 1, tested)
    )
    kwargs = {
        "revision": review["tool"]["revision"],
        "candidate_sha256": review["version"]["sha"],
        "approve": True,
    }
    if change == "hash":
        kwargs["candidate_sha256"] = "sha256:" + "0" * 64
    if change == "rollback":
        kwargs["rollback"] = True
    if change == "approval":
        kwargs["approve"] = False
    with pytest.raises(LibraryError):
        library.activate(tool, 1, **kwargs)
    assert library.published() == ()
    assert library.review(tool, 1)["approval"] is None


def test_publication_failure_rolls_back_all_changes(tmp_path, tested, monkeypatch):
    library, tool, _ = imported(tmp_path, tested)
    review = attach_version(library, tool, 1, tested)

    def fail(*args):
        raise RuntimeError("Simulated transaction failure")

    monkeypatch.setattr(library, "_audit", fail)
    with pytest.raises(RuntimeError):
        activate(library, review, approve=True)
    assert library.published() == ()
    after = library.review(tool, 1)
    assert after["approval"] is None
    assert after["version"]["state"] == "tested"
    assert after["tool"]["revision"] == review["tool"]["revision"]


def test_bad_cli_candidate_is_safe_and_never_registers(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("name: [SECRET-PARSER-CONTENT")
    assert (
        run(
            [
                "library",
                "--directory",
                str(tmp_path / "library"),
                "import",
                str(path),
                "--origin",
                "migration",
            ]
        )
        == 2
    )
    assert "SECRET-PARSER-CONTENT" not in capsys.readouterr().err
    assert Library(tmp_path / "library").list() == []


def test_source_file_and_external_evidence_changes_do_not_mutate_imported_version(tmp_path, tested):
    import shutil

    source = tmp_path / "copy.candidate"
    source.write_bytes(tested[0].read_bytes())
    evidence = tmp_path / "attempt"
    shutil.copytree(tested[1], evidence)
    library = Library(tmp_path / "library")
    review = library.import_draft(source, origin="migration")
    tool = review["tool"]["id"]
    library.attach_tests(tool, 1, evidence, tested[2])
    source.write_text("broken")
    shutil.rmtree(evidence)
    activate(library, library.review(tool, 1), approve=True)
    assert library.published()[0].manifest_digest == review["version"]["manifest"]
