"""Transactional library. Immutable source/evidence records; audited live pointers."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.evaluation.models import Report
from browser_to_mcp.evaluation.service import digest, read_bytes, verify_evaluation
from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.loader import LoadedWorkflow, Publication


class LibraryError(ValueError):
    """Safe local lifecycle error without candidate/parser contents."""


def now():
    return datetime.now(UTC).isoformat()


def snapshot(directory):
    if directory.is_symlink() or (directory / "traces").is_symlink():
        raise LibraryError("Evidence directories must not be symbolic links.")
    report = Report.model_validate_json(read_bytes(directory / "report.json"))
    names = ["report.json", "draft.candidate", "plan.json"]
    names += [o.trace_file for o in report.observations if o.trace_file]
    files = {name: read_bytes(directory / name).decode("utf-8") for name in names}
    value = json.dumps(files, sort_keys=True)
    if len(value.encode()) > 32 * 1024 * 1024:
        raise LibraryError("Evidence exceeds the 32 MiB library limit.")
    return value


def verify(source, evidence, pin):
    """Recreate only the closed set of evaluation files and use the existing verifier."""
    files = json.loads(evidence)
    report = Report.model_validate_json(files["report.json"])
    allowed = {"report.json", "draft.candidate", "plan.json"} | {
        o.trace_file for o in report.observations if o.trace_file
    }
    if set(files) != allowed:
        raise LibraryError("Unexpected evidence files.")
    with tempfile.TemporaryDirectory(prefix="browser-to-mcp-review-") as temporary:
        root = Path(temporary)
        (root / "traces").mkdir()
        for name, data in files.items():
            (root / name).write_text(data, encoding="utf-8")
        candidate = root / "candidate"
        candidate.write_text(source, encoding="utf-8")
        return verify_evaluation(candidate, root / "plan.json", root, pin)


class Library:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "library.sqlite3"
        if self.path.is_symlink():
            raise LibraryError("The library database must not be a symbolic link.")
        fd = (
            os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            if not self.path.exists()
            else None
        )
        if fd is not None:
            os.close(fd)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tools (
                    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
                    revision INTEGER NOT NULL, live INTEGER, latest INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (
                    tool TEXT NOT NULL REFERENCES tools(id), version INTEGER NOT NULL,
                    source TEXT NOT NULL, sha TEXT NOT NULL, manifest TEXT NOT NULL,
                    origin TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(tool,version));
                CREATE TABLE IF NOT EXISTS states (
                    tool TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(tool,version), FOREIGN KEY(tool,version) REFERENCES versions);
                CREATE TABLE IF NOT EXISTS evidence (
                    tool TEXT NOT NULL, version INTEGER NOT NULL, files TEXT NOT NULL,
                    pin TEXT NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(tool,version),
                    FOREIGN KEY(tool,version) REFERENCES versions);
                CREATE TABLE IF NOT EXISTS approvals (
                    tool TEXT NOT NULL, version INTEGER NOT NULL, evidence_sha TEXT NOT NULL,
                    approved TEXT NOT NULL, PRIMARY KEY(tool,version),
                    FOREIGN KEY(tool,version) REFERENCES versions);
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY, tool TEXT NOT NULL, version INTEGER,
                    action TEXT NOT NULL, at TEXT NOT NULL, revision INTEGER NOT NULL);
            """)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool(self, db, tool):
        row = db.execute("SELECT * FROM tools WHERE id=?", (tool,)).fetchone()
        if row is None:
            raise LibraryError("Unknown tool ID. Use library list.")
        return row

    def _version(self, db, tool, version):
        row = db.execute(
            """SELECT v.*,s.state FROM versions v JOIN states s
            ON v.tool=s.tool AND v.version=s.version WHERE v.tool=? AND v.version=?""",
            (tool, version),
        ).fetchone()
        if row is None:
            raise LibraryError("Unknown tool version.")
        return row

    def _audit(self, db, tool, version, action):
        db.execute("UPDATE tools SET revision=revision+1 WHERE id=?", (tool,))
        revision = self._tool(db, tool)["revision"]
        db.execute(
            "INSERT INTO audit(tool,version,action,at,revision) VALUES(?,?,?,?,?)",
            (tool, version, action, now(), revision),
        )

    def import_draft(self, candidate: Path, *, origin: str, tool_id=None, display_name=None):
        if not origin.strip() or len(origin) > 2000:
            raise LibraryError("Provide a short public draft origin, such as a build job ID.")
        source = read_bytes(candidate, 262144).decode("utf-8")
        loaded, _ = check_candidate(source)
        compile_workflow(loaded)
        with self.connection(write=True) as db:
            if tool_id is None:
                tool_id = uuid4().hex
                try:
                    db.execute(
                        "INSERT INTO tools VALUES(?,?,?,?,?,?)",
                        (
                            tool_id,
                            loaded.workflow.name,
                            display_name or loaded.workflow.name,
                            0,
                            None,
                            0,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise LibraryError(
                        "Callable name already exists. Import a new version by tool ID."
                    ) from None
            tool = self._tool(db, tool_id)
            if loaded.workflow.name != tool["name"]:
                raise LibraryError(
                    "Versions must keep the callable name. Create another tool to rename it."
                )
            version = tool["latest"] + 1
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?)",
                (
                    tool_id,
                    version,
                    source,
                    digest(source.encode()),
                    loaded.manifest_digest,
                    origin,
                    now(),
                ),
            )
            db.execute("INSERT INTO states VALUES(?,?,?)", (tool_id, version, "draft"))
            db.execute("UPDATE tools SET latest=? WHERE id=?", (version, tool_id))
            self._audit(db, tool_id, version, "import")
        return self.review(tool_id, version)

    def review(self, tool, version):
        with self.connection() as db:
            meta = dict(self._tool(db, tool))
            record = dict(self._version(db, tool, version))
            evidence = db.execute(
                "SELECT * FROM evidence WHERE tool=? AND version=?", (tool, version)
            ).fetchone()
            approval = db.execute(
                "SELECT * FROM approvals WHERE tool=? AND version=?", (tool, version)
            ).fetchone()
        loaded, _ = check_candidate(record.pop("source"))
        return {
            "tool": meta,
            "version": record,
            "workflow": loaded.workflow.model_dump(mode="json"),
            "evidence": json.loads(evidence["files"]) if evidence else None,
            "approval": dict(approval) if approval else None,
        }

    def attach_tests(self, tool, version, directory: Path, pin: str):
        files = snapshot(directory)
        with self.connection(write=True) as db:
            row = self._version(db, tool, version)
            if row["state"] != "draft":
                raise LibraryError("Only a fresh draft can receive evidence. Import a new version.")
            try:
                verify(row["source"], files, pin)
                state = "tested"
            except (ValueError, OSError, KeyError):
                state = "failed"
            db.execute(
                "INSERT INTO evidence VALUES(?,?,?,?,?)",
                (tool, version, files, pin, digest(files.encode())),
            )
            db.execute(
                "UPDATE states SET state=? WHERE tool=? AND version=?", (state, tool, version)
            )
            self._audit(db, tool, version, state)
        return self.review(tool, version)

    def activate(self, tool, version, *, revision, candidate_sha256, approve=False, rollback=False):
        with self.connection(write=True) as db:
            meta = self._tool(db, tool)
            row = self._version(db, tool, version)
            if meta["revision"] != revision or row["sha"] != candidate_sha256:
                raise LibraryError("Review is stale. Read the complete version and review again.")
            if not rollback and version != meta["latest"]:
                raise LibraryError(
                    "A newer draft exists. Review it or explicitly roll back an approved version."
                )
            evidence = db.execute(
                "SELECT * FROM evidence WHERE tool=? AND version=?", (tool, version)
            ).fetchone()
            approval = db.execute(
                "SELECT * FROM approvals WHERE tool=? AND version=?", (tool, version)
            ).fetchone()
            if not evidence or row["state"] not in {"tested", "retired", "published"}:
                raise LibraryError("Publication requires passing evidence for this exact version.")
            if rollback and not approval:
                raise LibraryError("Rollback requires a previously approved version.")
            if not rollback and not approve:
                raise LibraryError("Explicit approval of the complete review is required.")
            try:
                verify(row["source"], evidence["files"], evidence["pin"])
            except Exception:
                raise LibraryError(
                    "Saved test evidence no longer verifies. Import and test a new version."
                ) from None
            if approval and approval["evidence_sha"] != digest(evidence["files"].encode()):
                raise LibraryError("Approved evidence has changed.")
            db.execute(
                "INSERT OR IGNORE INTO approvals VALUES(?,?,?,?)",
                (tool, version, digest(evidence["files"].encode()), now()),
            )
            db.execute(
                "UPDATE states SET state='tested' WHERE tool=? AND state='published'", (tool,)
            )
            db.execute(
                "UPDATE states SET state='published' WHERE tool=? AND version=?", (tool, version)
            )
            db.execute("UPDATE tools SET live=? WHERE id=?", (version, tool))
            self._audit(db, tool, version, "rollback" if rollback else "publish")
        return self.review(tool, version)

    def retire(self, tool, *, revision):
        with self.connection(write=True) as db:
            meta = self._tool(db, tool)
            if meta["revision"] != revision or meta["live"] is None:
                raise LibraryError("Review is stale or the tool is already inactive.")
            db.execute(
                "UPDATE states SET state='retired' WHERE tool=? AND version=?", (tool, meta["live"])
            )
            db.execute("UPDATE tools SET live=NULL WHERE id=?", (tool,))
            self._audit(db, tool, meta["live"], "retire")
        return self.list()

    def list(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tools ORDER BY name")]

    def history(self, tool):
        with self.connection() as db:
            self._tool(db, tool)
            return [
                dict(row)
                for row in db.execute("SELECT * FROM audit WHERE tool=? ORDER BY sequence", (tool,))
            ]

    def published(self):
        """Load one consistent, validated snapshot; servers retain it until restart."""
        result = []
        with self.connection() as db:
            for meta in db.execute("SELECT * FROM tools WHERE live IS NOT NULL ORDER BY name"):
                row = self._version(db, meta["id"], meta["live"])
                approval = db.execute(
                    "SELECT * FROM approvals WHERE tool=? AND version=?", (meta["id"], meta["live"])
                ).fetchone()
                evidence = db.execute(
                    "SELECT * FROM evidence WHERE tool=? AND version=?", (meta["id"], meta["live"])
                ).fetchone()
                if row["state"] != "published" or not approval or not evidence:
                    raise LibraryError(
                        "Published version has incomplete approval records; no tools registered."
                    )
                if digest(evidence["files"].encode()) != approval["evidence_sha"]:
                    raise LibraryError("Published evidence changed; no tools registered.")
                verify(row["source"], evidence["files"], evidence["pin"])
                loaded, _ = check_candidate(row["source"])
                if (
                    loaded.manifest_digest != row["manifest"]
                    or digest(row["source"].encode()) != row["sha"]
                ):
                    raise LibraryError("Published version identity changed; no tools registered.")
                result.append(
                    LoadedWorkflow(
                        loaded.workflow,
                        loaded.manifest_digest,
                        Publication(meta["id"], meta["live"], row["sha"]),
                    )
                )
        return tuple(result)
