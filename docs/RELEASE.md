# Release checklist

Issue #15 prepares the first private V0 pre-release. The package and GitHub
release are intentionally available only to repository collaborators. A later
public release must wait until the M0–M3 acceptance criteria pass, including
the regression and readiness work tracked in issue #17.

## Before tagging

Complete the [three-site checks](RELEASE_CHECKS.md) and their open user requirements
before closing #66 or the local-release milestone.

1. Confirm the private-release scope is approved and no credentials or private
   browser state are included in the artifact.
2. Set the project version in `pyproject.toml` and the lock file to the exact
   PEP 440 pre-release version, for example `0.0.1a1`.
3. Run the clean-checkout commands in [`DEMO.md`](DEMO.md).
4. Run the full checks:

   ```bash
   uv run pytest
   uv run ruff check .
   uv run python scripts/readiness_report.py --runs 10
   uv build
   ```

5. Review the generated files under `dist/` and verify the example workflow,
   trace schema, and documentation are in the same commit.

## Tagging

After the private-release checks pass and the version is updated:

```bash
git tag v0.0.1a1
git push origin v0.0.1a1
```

The release workflow builds the source distribution and wheel and creates a
GitHub pre-release from the tagged commit. This private pre-release does not
change repository visibility. Before making a later release public, complete
the M3 gate in issue #17 and review the public security and onboarding docs.
The release workflow independently runs the readiness command, so a failed M3
fixture prevents artifact publication.
