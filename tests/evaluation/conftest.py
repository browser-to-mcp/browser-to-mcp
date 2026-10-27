import pytest
from evaluation_site import serve


@pytest.fixture
def evaluation_site():
    with serve() as site:
        yield site
