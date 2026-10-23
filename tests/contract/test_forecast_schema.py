import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text())


def validator() -> Draft202012Validator:
    schema = load_json("schemas/harvest-window.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_packaged_schema_is_canonical() -> None:
    assert (ROOT / "packages/contracts/harvest-window.schema.json").read_bytes() == (
        ROOT / "schemas/harvest-window.schema.json"
    ).read_bytes()


def test_example_validates_and_round_trips(forecast_document: dict[str, Any]) -> None:
    validator().validate(forecast_document)
    assert json.loads(json.dumps(forecast_document)) == forecast_document


def test_schema_rejects_unsupported_visual_claims(
    forecast_document: dict[str, Any],
) -> None:
    invalid = deepcopy(forecast_document)
    invalid["visual_capability"]["food_safety_inferred"] = True
    with pytest.raises(ValidationError):
        validator().validate(invalid)


def test_schema_rejects_unexpected_media_or_location_fields(
    forecast_document: dict[str, Any],
) -> None:
    invalid = deepcopy(forecast_document)
    invalid["raw_residential_media"] = "not allowed"
    invalid["exact_address"] = "not allowed"
    assert not validator().is_valid(invalid)
