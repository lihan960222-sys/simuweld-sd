import os
from pathlib import Path

import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel


@pytest.fixture(scope="session")
def model():
    path = Path(os.environ["SFMARC_TEST_DAT"])
    return SurfaceModel(Deck({path.name: path.read_bytes()}, path.name))
