"""Closed public job records and fixed, safe diagnostics."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class State(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting_for_input"
    REVIEW = "ready_for_review"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


TERMINAL = {State.FAILED, State.CANCELLED, State.COMPLETED}
TRANSITIONS = {
    State.QUEUED: {State.RUNNING, State.FAILED, State.CANCELLED},
    State.RUNNING: {State.WAITING, State.REVIEW, State.FAILED, State.CANCELLED, State.COMPLETED},
    State.WAITING: {State.QUEUED, State.FAILED, State.CANCELLED},
    State.REVIEW: {State.QUEUED, State.FAILED, State.CANCELLED},
    **{state: set() for state in TERMINAL},
}


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budgets(ClosedModel):
    wall_seconds: Annotated[int, Field(strict=True, ge=1, le=86400)] = 900
    browser_actions: Annotated[int, Field(strict=True, ge=0, le=1000)] = 0
    model_requests: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    tokens: Annotated[int, Field(strict=True, ge=0, le=1000000)] = 0
    cost_microusd: Annotated[int, Field(strict=True, ge=0, le=100000000)] = 0
    output_bytes: Annotated[int, Field(strict=True, ge=1, le=1048576)] = 262144


class Usage(ClosedModel):
    browser_actions: int = 0
    model_requests: int = 0
    tokens: int = 0
    cost_microusd: int = 0
    output_bytes: int = 0


QUESTION_TEXT = {
    "review_draft": (
        "Keep this checked draft? Keeping it does not run or publish it.",
        ["keep", "discard"],
    ),
    "output_shape": (
        "Should the draft return one record or a list of records?",
        ["single", "list"],
    ),
}
QuestionKind = Literal["review_draft", "output_shape"]


class Question(ClosedModel):
    id: str
    kind: QuestionKind
    prompt: str
    choices: list[str]


MESSAGES = {
    "invalid_request": "Use valid job arguments and declared limits.",
    "invalid_candidate": (
        "The candidate must be a supported public read-only workflow without secrets."
    ),
    "not_found": "This job does not exist or its retained record has expired.",
    "store_unavailable": "The local job store is unavailable or has an unsupported schema.",
    "conflict": (
        "This request key was already used for different work. Use a new key for a new attempt."
    ),
    "expired_key": (
        "This request was already handled and its job record expired. It was not restarted."
    ),
    "stale_answer": "This question is no longer current. Read the job again before answering.",
    "invalid_answer": (
        "Choose one of the current question's listed answers. Do not send credentials."
    ),
    "invalid_transition": "This job state does not allow that operation.",
    "cancelled": "The job was cancelled. No tool was published.",
    "interrupted": (
        "The worker stopped during this phase. Work was not retried; start a new attempt."
    ),
    "worker_failed": (
        "The job could not finish. Raw worker details were omitted. No tool was published."
    ),
    "worker_busy": "Another worker already owns this local job directory.",
    "budget_wall_seconds": (
        "The total time limit was reached, including time waiting for an answer."
    ),
    "budget_browser_actions": (
        "The browser-action limit was reached before another action could start."
    ),
    "budget_model_requests": (
        "The model-request limit was reached before another request could start."
    ),
    "budget_tokens": "The reserved token limit was reached before another request could start.",
    "budget_cost_microusd": (
        "The reserved cost limit was reached before another request could start."
    ),
    "budget_output_bytes": "The draft output-size limit was exceeded.",
    "capacity": (
        "The local store is full. Clean up expired jobs. If the request receipt limit is reached, "
        "choose a new private job directory for new attempts."
    ),
}


class JobError(Exception):
    def __init__(self, code: str):
        self.code = code
        self.message = MESSAGES[code]
        super().__init__(self.message)

    def payload(self):
        return {"status": "error", "code": self.code, "message": self.message}


class Failure(ClosedModel):
    code: str
    message: str


class Artifact(ClosedModel):
    filename: str
    sha256: str
    manifest_digest: str


class JobSnapshot(ClosedModel):
    id: str
    kind: Literal["prepare_workflow"]
    state: State
    revision: int
    stage: str
    created_at: float
    updated_at: float
    deadline: float
    budgets: Budgets
    usage: Usage
    question: Question | None
    cancel_requested: bool
    error: Failure | None
    artifact: Artifact | None
