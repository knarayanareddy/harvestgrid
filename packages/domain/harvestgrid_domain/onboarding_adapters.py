"""Adapters for the ``FR-HG-001`` onboarding ports.

Both adapters are local fixtures:

* :class:`InMemoryOnboardingStore` is a compare-and-set metadata store. It is not a
  database and holds no residential media, addresses, or credentials.
* :class:`JsonCultivarCatalog` reads versioned cultivar content from ``content/cultivars``.
  It never invents priors; a draft review status keeps forecasts experimental.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, TypeVar

from .onboarding import (
    APPROVED_REVIEW_STATUS,
    Cultivar,
    MemberRecord,
    OnboardingError,
    OrganizationRecord,
    TreeRecord,
)


class _Versioned(Protocol):
    @property
    def version(self) -> int: ...


_RecordT = TypeVar("_RecordT", bound=_Versioned)


def _put_versioned(records: dict[str, _RecordT], key: str, record: _RecordT) -> None:
    existing = records.get(key)
    expected = 0 if existing is None else existing.version + 1
    if record.version != expected:
        raise OnboardingError("stale_record_version", detail=key)
    records[key] = record


class InMemoryOnboardingStore:
    """Local fixture store implementing :class:`~harvestgrid_domain.onboarding.OnboardingStore`."""

    def __init__(self) -> None:
        self._organizations: dict[str, OrganizationRecord] = {}
        self._members: dict[str, MemberRecord] = {}
        self._trees: dict[str, TreeRecord] = {}

    def get_organization(self, organization_id: str) -> OrganizationRecord | None:
        return self._organizations.get(organization_id)

    def put_organization(self, record: OrganizationRecord) -> None:
        _put_versioned(self._organizations, record.organization_id, record)

    def get_member(self, member_id: str) -> MemberRecord | None:
        return self._members.get(member_id)

    def put_member(self, record: MemberRecord) -> None:
        _put_versioned(self._members, record.member_id, record)

    def get_tree(self, tree_id: str) -> TreeRecord | None:
        return self._trees.get(tree_id)

    def put_tree(self, record: TreeRecord) -> None:
        _put_versioned(self._trees, record.tree_id, record)

    def list_trees(self, organization_id: str) -> tuple[TreeRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self._trees.values()
                    if record.organization_id == organization_id
                ),
                key=lambda record: record.tree_id,
            )
        )


def _require_document_text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OnboardingError("cultivar_catalog_invalid", detail=key)
    return value.strip()


def _as_document(value: Any, *, detail: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OnboardingError("cultivar_catalog_invalid", detail=detail)
    return {str(key): item for key, item in value.items()}


class JsonCultivarCatalog:
    """Versioned cultivar catalog loaded from JSON documents.

    A cultivar is identified by its ``content_version``. Its ``review_status`` must be
    ``approved_operational_prior`` before the catalog reports an approved prior; anything
    else keeps forecast output experimental. The catalog never supplies agronomic values.
    """

    def __init__(self, documents: Iterable[Mapping[str, object]]) -> None:
        self._raw: dict[str, dict[str, object]] = {}
        self._cultivars: dict[str, Cultivar] = {}
        for document in documents:
            cultivar = self._parse(document)
            if cultivar.cultivar_id in self._cultivars:
                raise OnboardingError(
                    "cultivar_catalog_invalid", detail=f"duplicate:{cultivar.cultivar_id}"
                )
            self._raw[cultivar.cultivar_id] = deepcopy(dict(document))
            self._cultivars[cultivar.cultivar_id] = cultivar
        if not self._cultivars:
            raise OnboardingError("cultivar_catalog_invalid", detail="no_cultivars")

    @staticmethod
    def _parse(document: Mapping[str, object]) -> Cultivar:
        content_version = _require_document_text(document, "content_version")
        species = _require_document_text(document, "species")
        review_status = _require_document_text(document, "review_status")
        display_name_value = document.get("display_name")
        if display_name_value is not None and (
            not isinstance(display_name_value, str) or not display_name_value.strip()
        ):
            raise OnboardingError("cultivar_catalog_invalid", detail="display_name")
        display_name = (
            display_name_value.strip() if isinstance(display_name_value, str) else content_version
        )
        return Cultivar(
            cultivar_id=content_version,
            display_name=display_name,
            species=species,
            content_version=content_version,
            review_status=review_status,
            operational_prior_approved=review_status == APPROVED_REVIEW_STATUS,
        )

    @classmethod
    def from_text(cls, text: str) -> JsonCultivarCatalog:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise OnboardingError("cultivar_catalog_invalid", detail="invalid_json") from None
        return cls([_as_document(parsed, detail="document")])

    @classmethod
    def from_directory(cls, directory: Path) -> JsonCultivarCatalog:
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise OnboardingError("cultivar_catalog_invalid", detail="no_cultivar_files")
        documents: list[dict[str, object]] = []
        for path in paths:
            try:
                parsed = json.loads(path.read_text())
            except json.JSONDecodeError:
                raise OnboardingError(
                    "cultivar_catalog_invalid", detail=f"invalid_json:{path.name}"
                ) from None
            documents.append(_as_document(parsed, detail=f"document:{path.name}"))
        return cls(documents)

    def list_cultivars(self) -> tuple[Cultivar, ...]:
        return tuple(self._cultivars[key] for key in sorted(self._cultivars))

    def require_cultivar(self, cultivar_id: str) -> Cultivar:
        return self._cultivars[cultivar_id]

    def require_document(self, cultivar_id: str) -> dict[str, object]:
        """Return a defensive copy of the validated source document."""

        return deepcopy(self._raw[cultivar_id])
