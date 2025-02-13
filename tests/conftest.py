import pytest
import pathlib
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(scope="session")
def data_dir():
    return (pathlib.Path(__file__).parent / "data").resolve()