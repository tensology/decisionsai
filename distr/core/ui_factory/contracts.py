"""Typed screen contracts that bound local-model UI work."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable


class ContractValidationError(ValueError):
    """Raised when a screen contract cannot be compiled safely."""


class ScreenType(str, Enum):
    ADMIN = "admin-screen"
    CRUD = "crud-list-detail"
    CUSTOMER_PORTAL = "customer-portal"
    DASHBOARD = "dashboard"
    FUNNEL = "funnel"


@dataclass(frozen=True)
class FieldContract:
    name: str
    label: str
    field_type: str = "text"
    required: bool = False
    nested: bool = False

    @classmethod
    def from_value(cls, value: str | dict[str, Any]) -> "FieldContract":
        if isinstance(value, str):
            name = value.strip()
            return cls(name=name, label=name.replace("_", " ").strip().title())
        return cls(
            name=str(value.get("name") or "").strip(),
            label=str(value.get("label") or value.get("name") or "").strip(),
            field_type=str(value.get("type") or value.get("field_type") or "text").strip(),
            required=bool(value.get("required")),
            nested=bool(value.get("nested")),
        )


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    given: str
    when: str
    then: str
    verification: str

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "AcceptanceCriterion":
        return cls(
            criterion_id=str(value.get("id") or value.get("criterion_id") or "").strip(),
            given=str(value.get("given") or "").strip(),
            when=str(value.get("when") or "").strip(),
            then=str(value.get("then") or "").strip(),
            verification=str(value.get("verification") or "").strip(),
        )


@dataclass(frozen=True)
class ScreenContract:
    screen_id: str
    screen_type: ScreenType
    title: str
    route: str
    primary_action: str
    entity: str = "record"
    framework: str = "react"
    form_mode: str = "none"
    columns: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    fields: tuple[FieldContract, ...] = ()
    states: tuple[str, ...] = ("loading", "empty", "error", "populated")
    viewports: tuple[str, ...] = ("desktop", "mid", "mobile")
    theme_modes: tuple[str, ...] = ("light", "dark")
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    existing_components: tuple[str, ...] = ()
    allowed_files: tuple[str, ...] = ()
    anti_bloat_constraints: tuple[str, ...] = (
        "one-primary-action",
        "no-duplicate-title",
        "no-unsolicited-widgets",
        "token-only-visual-values",
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScreenContract":
        raw_type = str(value.get("screen_type") or value.get("screenType") or "").strip()
        try:
            screen_type = ScreenType(raw_type)
        except ValueError as exc:
            raise ContractValidationError(f"Unsupported screen_type: {raw_type or '<missing>'}") from exc
        contract = cls(
            screen_id=str(value.get("screen_id") or value.get("screenId") or "").strip(),
            screen_type=screen_type,
            title=str(value.get("title") or "").strip(),
            route=str(value.get("route") or "").strip(),
            primary_action=str(value.get("primary_action") or value.get("primaryAction") or "").strip(),
            entity=str(value.get("entity") or "record").strip(),
            framework=str(value.get("framework") or "react").strip().lower(),
            form_mode=str(value.get("form_mode") or value.get("formMode") or "none").strip().lower(),
            columns=_strings(value.get("columns")),
            filters=_strings(value.get("filters")),
            fields=tuple(FieldContract.from_value(item) for item in (value.get("fields") or [])),
            states=_strings(value.get("states") or ("loading", "empty", "error", "populated")),
            viewports=_strings(value.get("viewports") or ("desktop", "mid", "mobile")),
            theme_modes=_strings(value.get("theme_modes") or value.get("themeModes") or ("light", "dark")),
            acceptance_criteria=tuple(
                AcceptanceCriterion.from_value(item)
                for item in (value.get("acceptance_criteria") or value.get("acceptanceCriteria") or [])
            ),
            existing_components=_strings(value.get("existing_components") or value.get("existingComponents")),
            allowed_files=_strings(value.get("allowed_files") or value.get("allowedFiles")),
            anti_bloat_constraints=_strings(
                value.get("anti_bloat_constraints")
                or value.get("antiBloatConstraints")
                or cls.__dataclass_fields__["anti_bloat_constraints"].default
            ),
            metadata=dict(value.get("metadata") or {}),
        )
        contract.validate()
        return contract

    def validate(self) -> None:
        errors: list[str] = []
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", self.screen_id):
            errors.append("screen_id must be a lowercase hyphenated identifier")
        if not self.title:
            errors.append("title is required")
        if not self.route.startswith("/"):
            errors.append("route must begin with /")
        if not self.primary_action:
            errors.append("primary_action is required")
        if self.framework != "react":
            errors.append("only the react compiler is currently supported")
        if len(set(self.states)) != len(self.states):
            errors.append("states must be unique")
        required_states = {"loading", "error", "populated"}
        if self.screen_type in {ScreenType.CRUD, ScreenType.DASHBOARD, ScreenType.CUSTOMER_PORTAL}:
            required_states.add("empty")
        missing_states = sorted(required_states.difference(self.states))
        if missing_states:
            errors.append("missing required states: " + ", ".join(missing_states))
        if not {"desktop", "mobile"}.issubset(self.viewports):
            errors.append("desktop and mobile viewports are required")
        invalid_themes = sorted(set(self.theme_modes).difference({"light", "dark"}))
        if invalid_themes or not self.theme_modes:
            errors.append("theme_modes must contain light and/or dark")
        if self.screen_type is ScreenType.CRUD and not self.columns:
            errors.append("CRUD screens require at least one column")
        if self.form_mode == "modal" and (len(self.fields) > 5 or any(item.nested for item in self.fields)):
            errors.append("modal forms are limited to five non-nested fields")
        if self.form_mode not in {"none", "modal", "drawer", "page"}:
            errors.append("form_mode must be none, modal, drawer, or page")
        criterion_ids = [item.criterion_id for item in self.acceptance_criteria]
        if not criterion_ids:
            errors.append("at least one acceptance criterion is required")
        if len(set(criterion_ids)) != len(criterion_ids):
            errors.append("acceptance criterion ids must be unique")
        for criterion in self.acceptance_criteria:
            if not all((criterion.criterion_id, criterion.given, criterion.when, criterion.then, criterion.verification)):
                errors.append("each acceptance criterion requires id, given, when, then, and verification")
                break
        for path in self.allowed_files:
            parsed = PurePosixPath(path)
            if parsed.is_absolute() or ".." in parsed.parts:
                errors.append(f"allowed_files contains unsafe path: {path}")
        if errors:
            raise ContractValidationError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["screen_type"] = self.screen_type.value
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()[:16]


def _strings(value: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (value or []) if str(item).strip())
