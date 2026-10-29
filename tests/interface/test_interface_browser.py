import asyncio
import json
import os
from pathlib import Path
from time import monotonic

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pilot import PROPOSAL, pilot, plan
from playwright.async_api import async_playwright, expect
from test_security import interface


async def until(predicate):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out waiting for the existing worker/service.")


def test_browser_creates_previews_tests_publishes_and_runs_with_real_mcp(tmp_path):
    async def exercise(service, server, url):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(server.url)
            await page.get_by_role("button", name="New task", exact=True).click()
            await page.get_by_label("Tool name", exact=True).fill("book_lookup")
            await page.get_by_label("Starting URL", exact=True).fill(url)
            await page.get_by_label("What should the tool do?").fill(
                "Read the book title and displayed price."
            )
            await page.get_by_label("Allowed website hostnames, separated by commas").fill(
                "127.0.0.1"
            )
            await page.get_by_role("button", name="Add input", exact=True).click()
            row = page.locator("#input-rows .row")
            await row.get_by_label("Name", exact=True).fill("query")
            await row.get_by_label("Description", exact=True).fill("Book code in the q parameter")
            await row.get_by_label("Example value", exact=True).fill("one")
            await page.get_by_label("Result shape", exact=True).select_option("object")
            for index, (name, description) in enumerate(
                [("title", "Full book title"), ("price", "Displayed book price")]
            ):
                if index:
                    await page.get_by_role("button", name="Add result field", exact=True).click()
                row = page.locator("#field-rows .row").nth(index)
                await row.get_by_label("Name", exact=True).fill(name)
                await row.get_by_label("Description", exact=True).fill(description)
            await page.get_by_role("button", name="Start build", exact=True).click()
            job = await until(
                lambda: next(
                    (
                        j
                        for j in service.status()["jobs"]
                        if j.get("question", {}) and j["question"]["kind"] == "draft_proposal"
                    ),
                    None,
                )
            )
            before = service.jobs.read(job["id"])
            await page.reload()
            assert service.jobs.read(job["id"]) == before
            assert len(service.status()["jobs"]) == 1
            # The connected assistant supplies a proposal through the actual MCP adapter.
            params = StdioServerParameters(
                command=str(Path(__file__).parents[2] / ".venv/bin/browser-to-mcp"),
                args=["jobs", "--directory", str(service.jobs.directory), "server"],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    context = await client.call_tool("read_task_context", {"job_id": job["id"]})
                    assert not context.isError
                    answer = await client.call_tool(
                        "submit_task_proposal",
                        dict(
                            job_id=job["id"],
                            question_id=job["question"]["id"],
                            revision=job["revision"],
                            proposal=PROPOSAL,
                        ),
                    )
                    assert not answer.isError
            await until(lambda: service.jobs.read(job["id"])["state"] == "ready_for_review")
            await page.get_by_role("button", name="Build jobs", exact=True).click()
            await page.get_by_role("button", name="Inspect draft & schemas").click()
            draft = service.dispatch("draft", {"job_id": job["id"]})
            assert draft["output_schema"]["properties"]["outputs"]
            await page.locator("#draft-view textarea").fill('{"query":"one"}')
            await page.get_by_role("button", name="Run this draft preview").click()
            await until(
                lambda: any(
                    o["kind"] == "preview" and o["state"] == "completed"
                    for o in service.status()["operations"]
                )
            )
            assert service.status()["operations"][0]["result"]["outputs"] == {
                "result": {"title": "A Light in the Attic", "price": "£51.77"}
            }
            await page.get_by_role("button", name="Build jobs", exact=True).click()
            await page.get_by_role("button", name="keep", exact=True).click()
            await until(lambda: service.jobs.read(job["id"])["state"] == "completed")
            await page.get_by_role("button", name="Keep in library for independent tests").click()
            await expect(page.locator("#review-data")).to_contain_text("book_lookup")
            await page.get_by_label("JSON test plan", exact=True).fill(json.dumps(plan()))
            await page.get_by_label(
                "I reviewed the plan and its expected answers independently."
            ).check()
            await page.get_by_role("button", name="Evaluate this exact version").click()
            op = await until(
                lambda: next(
                    (
                        o
                        for o in service.status()["operations"]
                        if o["kind"] == "evaluate" and o["state"] in {"completed", "failed"}
                    ),
                    None,
                )
            )
            assert op["state"] == "completed", op
            assert op["result"]["report"]["eligible"], op["result"]["report"]
            await page.get_by_role("button", name="Review tested version").click()
            await page.get_by_label(
                "I reviewed the full contract, permissions, test outcomes and limitations."
            ).check()
            await page.get_by_role("button", name="Publish reviewed version").click()
            await until(lambda: service.library.list()[0]["live"] == 1)
            await page.get_by_role("button", name="Run tool", exact=True).click()
            await page.get_by_label("Inputs (JSON)", exact=True).fill('{"query":"two"}')
            await page.get_by_role("button", name="Run published version").click()
            op = await until(
                lambda: next(
                    (
                        o
                        for o in service.status()["operations"]
                        if o["kind"] == "run" and o["state"] == "completed"
                    ),
                    None,
                )
            )
            assert op["result"]["outputs"] == {
                "result": {"title": "Shakespeare's Sonnets", "price": "£20.66"}
            }
            assert op["result"]["publication"]["version"] == 1
            await page.reload()
            await page.get_by_role("button", name="Run history", exact=True).click()
            await expect(page.locator("#run-list")).to_contain_text("Shakespeare's Sonnets")
            await page.get_by_role("button", name="MCP setup", exact=True).click()
            await expect(page.locator("#mcp-config")).to_contain_text("published_tools")
            assert "__PROJECT__" not in await page.locator("#mcp-config").inner_text()
            # Corrected builds can explicitly append a draft version through the UI.
            from uuid import uuid4

            from browser_to_mcp.jobs.models import Budgets

            replacement = service.jobs.start(
                str(uuid4()), service.jobs.read_artifact(job["id"], "draft").decode(), Budgets()
            )
            await until(lambda: service.jobs.read(replacement["id"])["state"] == "ready_for_review")
            pending = service.jobs.read(replacement["id"])
            service.jobs.answer(
                pending["id"], pending["question"]["id"], pending["revision"], "keep"
            )
            await until(lambda: service.jobs.read(pending["id"])["state"] == "completed")
            await page.get_by_role("button", name="Build jobs", exact=True).click()
            replacement_card = page.locator("#job-list article").filter(
                has=page.get_by_role("heading", name=pending["id"] + " · completed", exact=True)
            )
            tool = service.library.list()[0]
            await replacement_card.get_by_label("Save draft to", exact=True).select_option(
                tool["id"]
            )
            await replacement_card.get_by_role(
                "button", name="Keep in library for independent tests"
            ).click()
            await until(lambda: service.library.list()[0]["latest"] == 2)
            assert service.library.list()[0]["live"] == 1
            await expect(
                page.get_by_role("button", name="Publish reviewed version")
            ).to_be_disabled()
            await page.get_by_role("button", name="Tools", exact=True).click()
            if os.environ.get("BROWSER_TO_MCP_UI_SCREENSHOT"):
                await page.screenshot(
                    path=os.environ["BROWSER_TO_MCP_UI_SCREENSHOT"], full_page=True
                )
            assert errors == []
            await browser.close()

    with pilot() as url, interface(tmp_path, worker=True) as (service, server):
        asyncio.run(exercise(service, server, url))
