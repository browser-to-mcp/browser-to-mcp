"""Replay an explicitly reviewed demonstration and pick visible output targets."""

from importlib.resources import files

from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from browser_to_mcp.authoring.plan import AuthoringError
from browser_to_mcp.browser_policy import SECRET_NAME, SECRET_VALUE
from browser_to_mcp.models import Manifest, Permissions, Risk, Workflow, WorkflowStep
from browser_to_mcp.recorder.draft import Target
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.replay import execute_steps
from browser_to_mcp.validation import validate_workflow_policy


class SelectionSession(RecordingSession):
    async def prepare(self, steps: list[dict]) -> None:
        if self.ended.is_set() or self.page is None:
            raise AuthoringError(
                "browser: Could not open the demonstration. Check allowed domains."
            )
        # This internal preview has no outputs yet. Every action still passes normal policy.
        workflow = Workflow.model_construct(
            name="authoring_preview",
            description="Reviewed demonstration",
            version=1,
            risk=Risk.READ_ONLY,
            manifest=Manifest(version=1),
            permissions=Permissions(allowed_domains=self.capture.domains),
            inputs={},
            outputs={},
            success=[],
            steps=[WorkflowStep.model_validate(step) for step in steps],
        )
        validate_workflow_policy(workflow)
        await execute_steps(
            self.page,
            workflow.model_copy(update={"steps": workflow.steps[1:]}),
            {},
            allowed_domains=self.capture.domains,
            boundary=self.boundary,
        )
        self.check_boundary()
        await self.install_selection()

    async def install_selection(self):
        self.check_boundary()
        await self.page.evaluate("() => window.__btmPause?.()")
        self._paused = True
        await self.page.evaluate(
            files("browser_to_mcp.authoring").joinpath("selection.js").read_text("utf-8")
        )

    def check_boundary(self) -> None:
        if self.boundary.failure:
            raise AuthoringError("browser: " + self.boundary.failure.message)
        if self.capture.incomplete or self.ended.is_set():
            raise AuthoringError(
                "browser: An unsupported action or blocked request occurred. "
                "Check the recording and explicit domains; create a new draft."
            )

    async def selected(self) -> tuple[Target, str]:
        self.check_boundary()
        try:
            selection = await self.page.evaluate("() => window.__btmSelection")
            if not selection:
                raise AuthoringError("output: Click the page text first, then press Enter here.")
            if "error" in selection:
                raise AuthoringError("output: " + selection["error"])
            target = Target.model_validate(selection["target"])
            if (
                SECRET_NAME.search(target.selector)
                or SECRET_VALUE.search(target.selector)
                or "{{" in target.selector
                or "}}" in target.selector
            ):
                raise AuthoringError(
                    "output: Choose a target without sensitive names or templates."
                )
            locator = self.page.locator(target.selector)
            if await locator.count() != 1 or not await locator.is_visible():
                raise AuthoringError(
                    "output: Target is missing, hidden, or ambiguous. Select again."
                )
            text = await locator.inner_text(timeout=5000)
            if SECRET_VALUE.search(text):
                raise AuthoringError("output: This text may contain a secret. Select public text.")
            return target, text
        except (PlaywrightError, ValidationError, TypeError, KeyError):
            raise AuthoringError(
                "output: Page changed or closed. Select again or cancel."
            ) from None

    async def reset_selection(self) -> None:
        await self.page.evaluate(
            "() => { window.__btmSelection = null; window.__btmPickedElement = null; }"
        )

    async def inspect_record(self, mode: str, **options):
        self.check_boundary()
        try:
            result = await self.page.evaluate(
                files("browser_to_mcp.authoring")
                .joinpath("record_selection.js")
                .read_text("utf-8"),
                {"mode": mode, **options},
            )
            if isinstance(result, dict) and "error" in result:
                raise AuthoringError("records: " + result["error"])
            entries = result if isinstance(result, list) else [result]
            for entry in entries:
                selector = entry.get("selector", "")
                if SECRET_NAME.search(selector) or SECRET_VALUE.search(selector):
                    raise AuthoringError("records: Select an area without sensitive names.")
            self.check_boundary()
            return result
        except PlaywrightError:
            raise AuthoringError("records: The page changed. Select the output again.") from None
