"""Closed test plans and evidence records; expected answers live outside the builder."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from browser_to_mcp.jobs.models import BuildReport

Coverage = Literal[
    "demonstration",
    "changed_inputs",
    "combined_filters",
    "zero_results",
    "invalid_input",
    "multiple_pages",
    "changed_page",
]
COVERAGE = tuple(Coverage.__args__)
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Status = Literal["passed", "failed", "blocked", "untested"]
Text = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ExpectedOutputs(Closed):
    outcome: Literal["outputs"]
    outputs: dict[str, JsonValue]


class ExpectedError(Closed):
    outcome: Literal["error"]
    category: Literal["input", "permission", "runtime", "assertion"]
    path: Text


class Case(Closed):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    covers: Annotated[list[Coverage], Field(min_length=1, max_length=7)]
    inputs: dict[str, JsonValue]
    expected: Annotated[ExpectedOutputs | ExpectedError, Field(discriminator="outcome")]
    required: bool = True
    skip_reason: Text | None = None
    blocked_reason: Text | None = None

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.covers)) != len(self.covers):
            raise ValueError("Coverage labels must be unique.")
        if self.skip_reason and self.blocked_reason:
            raise ValueError("Choose either skipped or blocked.")
        return self


class Suite(Closed):
    schema_version: Literal[1] = 1
    name: Text
    requirements_source: Text
    expectations_source: Text
    mode: Literal["controlled", "live"]
    timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 30
    wall_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    not_applicable: dict[Coverage, Text] = Field(default_factory=dict)
    cases: Annotated[list[Case], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique.")
        if set(self.not_applicable) & {tag for case in self.cases for tag in case.covers}:
            raise ValueError("A tested requirement cannot also be marked not applicable.")
        return self


class Observation(Closed):
    case_id: str
    status: Status
    reason: str
    seconds: Annotated[float, Field(ge=0)]
    outcome: Literal["outputs", "error", "none"]
    trace_file: Annotated[str, Field(pattern=r"^traces/[0-9a-f]{32}\.json$")] | None = None
    trace_sha256: Digest | None = None


class SourceVersion(Closed):
    package_version: str
    source_sha256: Digest
    git_commit: str | None
    worktree_dirty: bool | None
    python: str
    playwright: str
    platform: str


class Report(Closed):
    schema_version: Literal[1] = 1
    candidate_sha256: Digest
    manifest_digest: Digest
    workflow: str
    suite_sha256: Digest
    mode: Literal["controlled", "live"]
    started_at: str
    finished_at: str
    seconds: Annotated[float, Field(ge=0)]
    source: SourceVersion
    # Imported builder measurements, never inferred from replay or used to score answers.
    generation: BuildReport | None = None
    replay_model_requests: Literal[0] = 0
    replay_api_cost_microusd: Literal[0] = 0
    local_compute_cost_microusd: None = None
    observations: list[Observation]
    blockers: list[str]
    eligible: bool
