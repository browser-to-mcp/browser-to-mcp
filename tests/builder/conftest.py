import pytest
from builder_site import serve

from browser_to_mcp.jobs.store import JobStore


@pytest.fixture(autouse=True)
def isolate_provider_configuration(monkeypatch):
    # Worker subprocesses must never inherit a developer's live provider settings.
    for name in (
        "BROWSER_TO_MCP_PROVIDER",
        "BROWSER_TO_MCP_MODEL_URL",
        "BROWSER_TO_MCP_MODEL_API_KEY",
        "BROWSER_TO_MCP_MODEL_MAX_TOKENS",
        "OPENAI_API_KEY",
        "BROWSER_TO_MCP_OPENAI_MODEL",
        "BROWSER_TO_MCP_INPUT_MICROUSD_PER_MILLION",
        "BROWSER_TO_MCP_OUTPUT_MICROUSD_PER_MILLION",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def catalog_site():
    with serve() as site:
        yield site


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")
