import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text())


@pytest.fixture
def forecast_document() -> dict[str, Any]:
    return load_json("examples/harvest-window.example.json")
