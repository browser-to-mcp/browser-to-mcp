import pytest
from paging_site import serve


@pytest.fixture
def paging_site():
    with serve() as server:
        yield server
