import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def omm_sample() -> list[dict]:
    return json.loads((FIXTURES / "omm_sample.json").read_text(encoding="utf-8"))
