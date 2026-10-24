import pytest
from record_site import serve


@pytest.fixture
def records_site():
    with serve() as server:
        yield server
