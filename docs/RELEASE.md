# Release checklist

Issue #15 prepares the first public V0 demonstration. The actual release must
wait until the M0–M3 acceptance criteria pass, including the regression and
readiness work tracked in issue #17.

## Before tagging

1. Confirm the M3 readiness report is complete.
2. Confirm the repository owner has switched the repository to public.
3. Set the project version in `pyproject.toml` and the lock file to the exact
   PEP 440 pre-release version, for example `0.0.1a1`.
4. Run the clean-checkout commands in [`DEMO.md`](DEMO.md).
5. Run the full checks:

   ```bash
   uv run pytest
   uv run ruff check .
   uv build
   ```

6. Review the generated files under `dist/` and verify the example workflow,
   trace schema, and documentation are in the same commit.

## Tagging

After the gate passes and the version is updated:

```bash
git tag v0.0.1a1
git push origin v0.0.1a1
```

The release workflow builds the source distribution and wheel and creates a
GitHub pre-release from the tagged commit. Do not tag a release while the M3
gate or repository visibility requirement is incomplete.
