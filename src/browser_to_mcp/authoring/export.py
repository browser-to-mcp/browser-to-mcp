"""Validate the exact bytes before an exclusive, atomic publication."""

import os
import tempfile
from pathlib import Path

import yaml

from browser_to_mcp.authoring.plan import AuthoringError
from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.loader import LoadedWorkflow, load_workflow


def export_workflow(loaded: LoadedWorkflow, destination: Path, *, reviewed: bool) -> Path:
    if not reviewed:
        raise AuthoringError("review: Finish review before saving a runnable workflow.")
    destination = destination.expanduser().absolute()
    if destination.suffix not in {".yaml", ".yml"}:
        raise AuthoringError("destination: Choose a .yaml or .yml file.")
    if destination.exists() or destination.is_symlink():
        raise AuthoringError("destination: File already exists. Choose a new filename.")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".review-", dir=destination.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(
                loaded.workflow.model_dump(mode="json", exclude_none=True),
                stream,
                sort_keys=False,
                allow_unicode=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        checked = load_workflow(Path(temporary))
        compile_workflow(checked)
        if checked.manifest_digest != loaded.manifest_digest:
            raise AuthoringError("review: Workflow changed. Review it again before saving.")
        os.link(temporary, destination)
    except OSError:
        raise AuthoringError(
            "destination: Could not save. Create the parent directory and choose an unused file."
        ) from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return destination
