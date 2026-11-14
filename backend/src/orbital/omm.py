"""Parse CCSDS OMM records as served by CelesTrak in JSON.

Catalog numbers passed 99999 in July 2026 and will grow to nine digits, so
NORAD_CAT_ID is always handled as a plain int — never a fixed-width string.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class OmmRecord:
    norad_cat_id: int
    object_name: str | None
    object_id: str | None  # international designator, e.g. "1998-067A"
    epoch: datetime  # UTC
    mean_motion: float  # rev/day
    eccentricity: float
    inclination: float  # deg
    ra_of_asc_node: float  # deg
    arg_of_pericenter: float  # deg
    mean_anomaly: float  # deg
    bstar: float
    mean_motion_dot: float
    mean_motion_ddot: float
    ephemeris_type: int
    classification_type: str
    element_set_no: int | None
    rev_at_epoch: int | None

    def as_row(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__slots__}


def parse_epoch(value: str) -> datetime:
    """OMM EPOCH is ISO 8601 in UTC, usually without a zone suffix."""
    dt = datetime.fromisoformat(value.removesuffix("Z"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _opt_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def parse_record(raw: dict[str, Any]) -> OmmRecord:
    return OmmRecord(
        norad_cat_id=int(raw["NORAD_CAT_ID"]),
        object_name=raw.get("OBJECT_NAME") or None,
        object_id=raw.get("OBJECT_ID") or None,
        epoch=parse_epoch(raw["EPOCH"]),
        mean_motion=float(raw["MEAN_MOTION"]),
        eccentricity=float(raw["ECCENTRICITY"]),
        inclination=float(raw["INCLINATION"]),
        ra_of_asc_node=float(raw["RA_OF_ASC_NODE"]),
        arg_of_pericenter=float(raw["ARG_OF_PERICENTER"]),
        mean_anomaly=float(raw["MEAN_ANOMALY"]),
        bstar=float(raw.get("BSTAR", 0)),
        mean_motion_dot=float(raw.get("MEAN_MOTION_DOT", 0)),
        mean_motion_ddot=float(raw.get("MEAN_MOTION_DDOT", 0)),
        ephemeris_type=int(raw.get("EPHEMERIS_TYPE", 0)),
        classification_type=raw.get("CLASSIFICATION_TYPE") or "U",
        element_set_no=_opt_int(raw.get("ELEMENT_SET_NO")),
        rev_at_epoch=_opt_int(raw.get("REV_AT_EPOCH")),
    )


def parse_records(raws: list[dict[str, Any]]) -> tuple[list[OmmRecord], list[tuple[Any, str]]]:
    """Parse a batch; bad records are returned as (catalog id, error) rather than aborting the run."""
    good: list[OmmRecord] = []
    bad: list[tuple[Any, str]] = []
    for raw in raws:
        try:
            good.append(parse_record(raw))
        except (KeyError, TypeError, ValueError) as exc:
            bad.append((raw.get("NORAD_CAT_ID"), f"{type(exc).__name__}: {exc}"))
    return good, bad
