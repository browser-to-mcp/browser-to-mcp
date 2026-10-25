import pytest
from builder_site import serve

from browser_to_mcp.jobs.store import JobStore


@pytest.fixture
def catalog_site():
    with serve() as site:
        yield site


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")
