"""Replay an explicitly reviewed demonstration and pick visible output targets."""

from importlib.resources import files

from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from browser_to_mcp.authoring.plan import AuthoringError
from browser_to_mcp.models import Manifest, Permissions, Risk, Workflow, WorkflowStep
from browser_to_mcp.recorder.draft import SECRET_NAME, SECRET_VALUE, Target
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
        )
        self.check_boundary()
        await self.page.evaluate("() => window.__btmPause?.()")
        self._paused = True
        await self.page.evaluate(
            files("browser_to_mcp.authoring").joinpath("selection.js").read_text("utf-8")
        )

    def check_boundary(self) -> None:
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
        await self.page.evaluate("() => { window.__btmSelection = null; }")
