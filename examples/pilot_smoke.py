"""Opt-in live pilot observations, separate from deterministic CI."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow

CASES = [
    ("a-light-in-the-attic_1000", {"title": "A Light in the Attic", "price": "£51.77"}),
    ("shakespeares-sonnets_989", {"title": "Shakespeare's Sonnets", "price": "£20.66"}),
    ("browser-to-mcp-missing_0", None),
]


async def observe(workflow: Path, trace_dir: Path) -> dict:
    loaded = load_workflow(workflow)
    results = []
    started = monotonic()
    for slug, expected in CASES:
        before = monotonic()
        item = {"input": {"product_slug": slug}, "expected_outputs": expected}
        try:
            result = await replay_workflow(loaded, [f"product_slug={slug}"], trace_dir, 60)
            item.update(
                outputs=result.outputs,
                correct=result.outputs == expected,
                trace_path=result.trace_path,
            )
        except WorkflowExecutionError as error:
            item.update(
                category=error.category.value,
                path=error.path,
                correct=expected is None
                and error.category.value == "runtime"
                and error.path == "steps.title",
                trace_path=error.trace_path,
            )
        item["duration_seconds"] = round(monotonic() - before, 3)
        results.append(item)
    return dict(
        date=datetime.now(UTC).isoformat(),
        kind="opt-in live observation",
        attempts=len(results),
        correct_outcomes=sum(r["correct"] for r in results),
        unexpected_outcomes=sum(not r["correct"] for r in results),
        duration_seconds=round(monotonic() - started, 3),
        manifest_digest=loaded.manifest_digest,
        results=results,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--trace-dir", type=Path, default=Path("traces/pilot-live"))
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(observe(args.workflow, args.trace_dir)), indent=2, ensure_ascii=False
        )
    )
