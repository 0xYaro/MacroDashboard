import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from macro_migrate import model, verify  # noqa: E402


@pytest.fixture(scope="session")
def build():
    return model.build_all()


@pytest.fixture(scope="session")
def checks(build):
    return verify.all_checks(build)
