from pathlib import Path

import pytest
import yaml

from browser_to_mcp.jobs.store import JobStore


@pytest.fixture
def candidate():
    data = yaml.safe_load(Path("workflows/hn_search.yaml").read_text())
    data["permissions"]["allowed_domains"] = ["127.0.0.1"]
    for step in data["steps"]:
        if step["action"] == "goto":
            step["url"] = "http://127.0.0.1/"
    return yaml.safe_dump(data, sort_keys=False)


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")
