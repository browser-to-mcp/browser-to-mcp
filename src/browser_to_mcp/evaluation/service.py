"""Run independent plans and recheck evidence without repeating website actions."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import monotonic

from pydantic import ValidationError

from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.models import StructuredOutput
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow
from browser_to_mcp.tracing import WorkflowTrace, has_sensitive_outputs

from .models import (
    COVERAGE,
    ExpectedError,
    ExpectedOutputs,
    Observation,
    Report,
    SourceVersion,
    Suite,
)


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_bytes(path: Path, limit=2_097_152) -> bytes:
    if path.is_symlink():
        raise ValueError("Evidence files must not be symbolic links.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds the supported size limit.")
    return data


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("JSON numbers must be finite.")

    def number(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite.")
        return value

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def read_json(path: Path):
    return _json(read_bytes(path))


def _prepare(candidate: Path, suite_path: Path, expected_suite_sha256: str):
    candidate_data = read_bytes(candidate, 262144)
    loaded, _ = check_candidate(candidate_data.decode("utf-8"))
    if any(item.sensitive for item in loaded.workflow.inputs.values()) or has_sensitive_outputs(
        loaded.workflow
    ):
        raise ValueError("This evaluator accepts public inputs and outputs only.")
    compiled = compile_workflow(loaded)
    suite_data = read_bytes(suite_path)
    if digest(suite_data) != expected_suite_sha256:
        raise ValueError("The test plan differs from the independently approved plan hash.")
    # Parse the same bytes whose hash was checked, even if the path changes later.
    suite = Suite.model_validate(_json(suite_data))
    for case in suite.cases:
        if isinstance(case.expected, ExpectedOutputs):
            outputs_model = compiled.output_model.model_fields["outputs"].annotation
            outputs_model.model_validate(case.expected.outputs)
    return loaded, compiled, suite, digest(candidate_data)


def _exact(actual, expected):
    """JSON equality including types and row order (False must not equal 0)."""
    return _difference(actual, expected) is None


def _difference(actual, expected, path="outputs"):
    if type(actual) is not type(expected):
        return path
    if isinstance(actual, dict):
        if actual.keys() != expected.keys():
            return path
        children = ((actual[key], expected[key], f"{path}.{key}") for key in expected)
    elif isinstance(actual, list):
        if len(actual) != len(expected):
            return path
        children = ((a, e, f"{path}.{i}") for i, (a, e) in enumerate(zip(actual, expected)))
    else:
        return None if actual == expected else path
    for a, e, p in children:
        found = _difference(a, e, p)
        if found:
            return found
    return None


def _inputs(compiled, case):
    try:
        return compiled.input_model.model_validate(case.inputs).model_dump(exclude_none=True)
    except ValidationError:
        return None


def _score(case, trace, mode):
    if trace.status == "completed":
        if trace.error is not None:
            return "failed", "A completed trace contains an error."
        if not isinstance(case.expected, ExpectedOutputs):
            return "failed", "The tool returned data when a refusal was required."
        mismatch = _difference(trace.outputs, case.expected.outputs)
        if mismatch:
            return "failed", f"Expected value, type, fields or row order differs at {mismatch}."
        return "passed", "Every expected output value and type matched."
    if trace.outputs or trace.error is None:
        return "failed", "A failed run returned partial data or has no error reason."
    if mode == "live" and trace.error.category == "runtime":
        return "blocked", "The live page could not be read; this is not a correct data result."
    if (
        isinstance(case.expected, ExpectedError)
        and trace.error.category == case.expected.category
        and trace.error.path == case.expected.path
    ):
        return "passed", "The exact required refusal matched; no successful data was returned."
    return "failed", "The run failed instead of returning the required outcome."


def _coverage_blockers(workflow, suite, observations):
    """A small or selectively skipped suite must never appear complete."""
    required = {"demonstration", "changed_page"}
    if workflow.inputs:
        required |= {"changed_inputs", "invalid_input"}
    if len(workflow.inputs) > 1:
        required.add("combined_filters")
    if any(
        isinstance(o, StructuredOutput) and o.type == "array" for o in workflow.outputs.values()
    ):
        required.add("zero_results")
    if any(step.paging for step in workflow.steps):
        required.add("multiple_pages")
    blockers = []
    by_id = {item.case_id: item for item in observations}
    for tag in COVERAGE:
        cases = [c for c in suite.cases if tag in c.covers and c.required]
        if tag in suite.not_applicable:
            if tag in required:
                blockers.append(f"{tag}: required for this candidate; cannot be waived.")
        elif not cases:
            blockers.append(
                f"{tag}: untested; add a required case or explain why it does not apply."
            )
        elif not all(by_id[c.id].status == "passed" for c in cases):
            blockers.append(f"{tag}: required checks have not all passed.")
    for case in suite.cases:
        if case.required and by_id[case.id].status != "passed":
            blockers.append(f"{case.id}: {by_id[case.id].status}.")

    demonstrations = [
        c
        for c in suite.cases
        if c.required and "demonstration" in c.covers and isinstance(c.expected, ExpectedOutputs)
    ]
    if not demonstrations:
        blockers.append("demonstration: a known successful result is required.")
    for name in workflow.inputs:
        if not any(
            c.required
            and "changed_inputs" in c.covers
            and isinstance(c.expected, ExpectedOutputs)
            and name in c.inputs
            and name in demo.inputs
            and not _exact(c.inputs[name], demo.inputs[name])
            and not _exact(c.expected.outputs, demo.expected.outputs)
            for c in suite.cases
            for demo in demonstrations
        ):
            blockers.append(f"changed_inputs: no different input and answer for {name}.")
    if "combined_filters" in required and not any(
        c.required
        and "combined_filters" in c.covers
        and isinstance(c.expected, ExpectedOutputs)
        and sum(not _exact(c.inputs.get(n), demo.inputs.get(n)) for n in workflow.inputs) >= 2
        and not _exact(c.expected.outputs, demo.expected.outputs)
        for c in suite.cases
        for demo in demonstrations
    ):
        blockers.append("combined_filters: no case changes two inputs and the expected answer.")
    if "invalid_input" in required and not any(
        c.required
        and "invalid_input" in c.covers
        and isinstance(c.expected, ExpectedError)
        and c.expected.category == "input"
        for c in suite.cases
    ):
        blockers.append("invalid_input: an explicit input refusal is required.")
    if "zero_results" in required and not any(
        c.required
        and "zero_results" in c.covers
        and (
            isinstance(c.expected, ExpectedOutputs)
            and any(
                isinstance(workflow.outputs.get(name), StructuredOutput)
                and workflow.outputs[name].type == "array"
                and value == []
                for name, value in c.expected.outputs.items()
            )
            or isinstance(c.expected, ExpectedError)
        )
        for c in suite.cases
    ):
        blockers.append("zero_results: an empty result or explicit refusal is required.")
    if "multiple_pages" in required:
        status_names = [step.paging.status_output for step in workflow.steps if step.paging]
        if not any(
            c.required
            and "multiple_pages" in c.covers
            and isinstance(c.expected, ExpectedOutputs)
            and all(
                isinstance(c.expected.outputs.get(name), dict)
                and c.expected.outputs[name].get("pages_read", 0) >= 2
                and c.expected.outputs[name].get("complete") is True
                for name in status_names
            )
            for c in suite.cases
        ):
            blockers.append(
                "multiple_pages: a complete result from at least two pages is required."
            )
    return blockers


def _source_version():
    root = Path(__file__).resolve().parents[1]
    source = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.suffix in {".py", ".js"}):
        source.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    commit, dirty = None, None
    try:
        # Installed wheels may have no checkout. Never attribute a parent project's Git revision.
        repo = root.parents[1]
        if (repo / ".git").exists():
            commit = (
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            dirty = bool(
                subprocess.check_output(
                    ["git", "-C", str(repo), "status", "--porcelain"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
    except (OSError, subprocess.SubprocessError):
        pass
    return SourceVersion(
        package_version=version("browser-to-mcp"),
        source_sha256="sha256:" + source.hexdigest(),
        git_commit=commit,
        worktree_dirty=dirty,
        python=sys.version.split()[0],
        playwright=version("playwright"),
        platform=platform.platform(),
    )


def _save_report(report, directory):
    with (directory / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2) + "\n")
    lines = [
        f"# {report.workflow}: {'passed' if report.eligible else 'not ready'}",
        "",
        f"Candidate: `{report.candidate_sha256}`",
        f"Plan: `{report.suite_sha256}`",
        f"Mode: {report.mode}",
        f"Elapsed: {report.seconds:.3f} seconds",
        "Details and runtime versions: [report.json](report.json)",
        "",
        "| Case | Check | Observed outcome | Reason | Trace |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {o.case_id} | {o.status} | {o.outcome} | "
        + html.escape(o.reason).replace("|", "&#124;").replace("\n", " ")
        + (f" | [trace]({o.trace_file}) |" if o.trace_file else " | — |")
        for o in report.observations
    ]
    lines += [
        "",
        "A passed refusal checks safe failure; it is not a successful data result.",
        "",
        "Generation usage: see report.json; missing measurements are unknown.",
        "Replay uses no model API. Local compute cost is not measured.",
        "",
    ]
    lines += (
        ["Blocking approval:", "", *[f"- {b}" for b in report.blockers]]
        if report.blockers
        else [
            "All required checks passed for this candidate and plan. Publishing still needs review."
        ]
    )
    with (directory / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


async def evaluate(candidate, suite_path, directory, expected_suite_sha256, *, generation=None):
    """Use normal typed inputs and replay; no model, retries, publication or answer edits."""
    loaded, compiled, suite, candidate_hash = _prepare(candidate, suite_path, expected_suite_sha256)
    directory.mkdir(mode=0o700)  # One new directory per attempt, including failed attempts.
    traces = directory / "traces"
    traces.mkdir(mode=0o700)
    started_at, started = datetime.now(UTC).isoformat(), monotonic()
    observations = []
    source = _source_version()
    try:
        async with asyncio.timeout(suite.wall_seconds):
            for case in suite.cases:
                if case.skip_reason or case.blocked_reason:
                    observations.append(
                        Observation(
                            case_id=case.id,
                            status="blocked" if case.blocked_reason else "untested",
                            reason=case.blocked_reason or case.skip_reason,
                            seconds=0.0,
                            outcome="none",
                        )
                    )
                    continue
                started_case = monotonic()
                values = _inputs(compiled, case)
                if values is None:
                    matches = isinstance(case.expected, ExpectedError) and (
                        case.expected.category == "input" and case.expected.path == "inputs"
                    )
                    observations.append(
                        Observation(
                            case_id=case.id,
                            status="passed" if matches else "failed",
                            outcome="error",
                            reason="Typed inputs were rejected before browsing; no trace needed.",
                            seconds=monotonic() - started_case,
                        )
                    )
                    continue
                pairs = [
                    f"{key}={str(v).lower() if isinstance(v, bool) else v}"
                    for key, v in values.items()
                ]
                previous_traces = set(traces.glob("*.json"))
                try:
                    result = await replay_workflow(loaded, pairs, traces, suite.timeout_seconds)
                    trace_path = Path(result.trace_path)
                except asyncio.CancelledError:
                    retained = set(traces.glob("*.json")) - previous_traces
                    path = retained.pop() if len(retained) == 1 else None
                    observations.append(
                        Observation(
                            case_id=case.id,
                            status="untested",
                            outcome="error" if path else "none",
                            reason="This check was interrupted before it finished.",
                            seconds=monotonic() - started_case,
                            trace_file="traces/" + path.name if path else None,
                            trace_sha256=digest(read_bytes(path)) if path else None,
                        )
                    )
                    raise
                except WorkflowExecutionError as error:
                    trace_path = Path(error.trace_path) if error.trace_path else None
                if trace_path is None:
                    raise ValueError("Replay did not retain a trace.")
                data = read_bytes(trace_path)
                trace = WorkflowTrace.model_validate(_json(data))
                status, reason = _score(case, trace, suite.mode)
                observations.append(
                    Observation(
                        case_id=case.id,
                        status=status,
                        reason=reason,
                        outcome="outputs" if trace.status == "completed" else "error",
                        seconds=monotonic() - started_case,
                        trace_file="traces/" + trace_path.name,
                        trace_sha256=digest(data),
                    )
                )
    finally:
        completed = {o.case_id for o in observations}
        observations += [
            Observation(
                case_id=c.id,
                status="untested",
                reason="The attempt stopped before this check finished.",
                seconds=0.0,
                outcome="none",
            )
            for c in suite.cases
            if c.id not in completed
        ]
        blockers = _coverage_blockers(loaded.workflow, suite, observations)
        report = Report(
            candidate_sha256=candidate_hash,
            manifest_digest=loaded.manifest_digest,
            workflow=loaded.workflow.name,
            suite_sha256=expected_suite_sha256,
            mode=suite.mode,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            seconds=monotonic() - started,
            source=source,
            generation=generation,
            observations=observations,
            blockers=blockers,
            eligible=not blockers,
        )
        _save_report(report, directory)
    if report.eligible:
        verify_evaluation(candidate, suite_path, directory, expected_suite_sha256)
    return report


def verify_evaluation(candidate, suite_path, directory, expected_suite_sha256):
    """Publication seam for #64. Recompute verdicts from the approved plan and saved traces."""
    loaded, compiled, suite, candidate_hash = _prepare(candidate, suite_path, expected_suite_sha256)
    if directory.is_symlink() or (directory / "traces").is_symlink():
        raise ValueError("The evidence directory must not be a symbolic link.")
    report = Report.model_validate(read_json(directory / "report.json"))
    if (
        report.candidate_sha256 != candidate_hash
        or report.manifest_digest != loaded.manifest_digest
        or report.workflow != loaded.workflow.name
        or report.suite_sha256 != expected_suite_sha256
        or report.mode != suite.mode
    ):
        raise ValueError("This report belongs to a different candidate or test plan.")
    if [o.case_id for o in report.observations] != [c.id for c in suite.cases]:
        raise ValueError("The report must contain every planned case exactly once, in order.")
    for case, observation in zip(suite.cases, report.observations, strict=True):
        if observation.status != "passed":
            continue
        if case.skip_reason or case.blocked_reason:
            raise ValueError("An unavailable case cannot pass.")
        values = _inputs(compiled, case)
        if values is None:
            if not (
                isinstance(case.expected, ExpectedError)
                and case.expected.category == "input"
                and case.expected.path == "inputs"
                and observation.outcome == "error"
                and observation.trace_file is None
                and observation.trace_sha256 is None
            ):
                raise ValueError("The input-refusal evidence does not match the test plan.")
            continue
        if not observation.trace_file or not observation.trace_sha256:
            raise ValueError("A browser result requires a retained trace and its hash.")
        data = read_bytes(directory / observation.trace_file)
        if digest(data) != observation.trace_sha256:
            raise ValueError("A saved trace has changed.")
        trace = WorkflowTrace.model_validate(_json(data))
        if (
            trace.metadata.manifest_digest != loaded.manifest_digest
            or trace.metadata.workflow_name != loaded.workflow.name
            or trace.metadata.workflow_version != loaded.workflow.version
            or trace.metadata.run_id != Path(observation.trace_file).stem
            or trace.metadata.python_version != report.source.python
            or trace.metadata.playwright_version != report.source.playwright
            or not _exact(trace.inputs, values)
            or _score(case, trace, suite.mode)[0] != "passed"
            or observation.outcome != ("outputs" if trace.status == "completed" else "error")
        ):
            raise ValueError("The trace identity, inputs or actual outcome do not match.")
        if trace.status == "completed" and (
            any(a.status != "completed" for a in trace.actions)
            or any(a.status != "passed" for a in trace.assertions)
        ):
            raise ValueError("A successful result has failed actions or assertions.")
    blockers = _coverage_blockers(loaded.workflow, suite, report.observations)
    if report.blockers != blockers or report.eligible != (not blockers):
        raise ValueError("The saved verdict differs from the required checks.")
    if blockers:
        raise ValueError("Required checks have not passed: " + " ".join(blockers))
    return report
