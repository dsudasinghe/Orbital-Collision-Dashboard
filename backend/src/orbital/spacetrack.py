"""Space-Track client: full GP catalog (gp) and public conjunction messages (cdm_public).

Limits (space-track.org/documentation, checked Nov 2026): under 30 requests/min
and 300/hour overall; gp at most 1/hour; all-constellation CDM queries at most
3/day. Each fetch logs in, makes one query and logs out.

The user agreement restricts redistribution, so raw messages stay in a private
table; the public site only shows derived stats and links.
"""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Connection, text

from orbital.cache import CachedText, cached_download
from orbital.config import Settings
from orbital.ingest import copy_rows

BASE_URL = "https://www.space-track.org"
CDM_MIN_INTERVAL_S = 8 * 60 * 60
GP_MIN_INTERVAL_S = 8 * 60 * 60  # allowed hourly; 3/day is plenty for nightly screening


class SpaceTrackError(RuntimeError):
    pass


def _query(settings: Settings, path: str) -> str:
    if not (settings.spacetrack_identity and settings.spacetrack_password):
        raise SpaceTrackError("SPACETRACK_IDENTITY and SPACETRACK_PASSWORD must be set in .env")
    with httpx.Client(base_url=BASE_URL, timeout=120, headers={"User-Agent": settings.celestrak_user_agent}) as client:
        login = client.post("/ajaxauth/login", data={
            "identity": settings.spacetrack_identity,
            "password": settings.spacetrack_password.get_secret_value(),
        })
        # A failed login still answers 200, with {"Login": "Failed"} in the body.
        if login.status_code != 200 or "Failed" in login.text:
            raise SpaceTrackError(f"login failed (HTTP {login.status_code})")
        try:
            resp = client.get(path)
            resp.raise_for_status()
            return resp.text
        finally:
            client.get("/ajaxauth/logout")


def _parse(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        raise SpaceTrackError(f"non-JSON response: {text[:200]!r}") from None
    if isinstance(data, dict) and "error" in {k.lower() for k in data}:
        raise SpaceTrackError(f"API error: {data}")
    if not isinstance(data, list):
        raise SpaceTrackError(f"unexpected payload: {type(data).__name__}")
    return data


def fetch_cdm_public(settings: Settings, *, days: int = 1, force: bool = False) -> tuple[list[dict[str, Any]], CachedText]:
    """Messages created in the last `days` days, oldest first."""
    path = (
        f"/basicspacedata/query/class/cdm_public/CREATED/%3Enow-{days}"
        "/orderby/CREATED%20asc/format/json/emptyresult/show"
    )
    cached = cached_download(
        settings.data_dir / "spacetrack" / "cdm_public", "json",
        min_interval_s=CDM_MIN_INTERVAL_S, force=force,
        download=lambda: _query(settings, path), validate=_parse,
    )
    return _parse(cached.text), cached


def fetch_gp_catalog(settings: Settings, *, force: bool = False) -> tuple[list[dict[str, Any]], CachedText]:
    """Latest OMM for every object still in orbit, debris included.

    This is Space-Track's recommended full-catalog query: not decayed, with an
    element set from the last 30 days. Records use the same OMM keywords as CelesTrak.
    """
    path = (
        "/basicspacedata/query/class/gp/decay_date/null-val/epoch/%3Enow-30"
        "/orderby/norad_cat_id/format/json"
    )
    cached = cached_download(
        settings.data_dir / "spacetrack" / "gp", "json",
        min_interval_s=GP_MIN_INTERVAL_S, force=force,
        download=lambda: _query(settings, path), validate=_parse,
    )
    return _parse(cached.text), cached


def fetch_gp_history(settings: Settings, ids: list[int], days: int) -> tuple[list[dict[str, Any]], CachedText]:
    """Every element set for `ids` with epoch in the last `days` days, in one query.

    Space-Track asks that history be downloaded once and stored, so this is a
    one-off backfill, cached locally and never scheduled.
    """
    path = (
        f"/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{','.join(map(str, sorted(ids)))}"
        f"/EPOCH/%3Enow-{days}/orderby/NORAD_CAT_ID,EPOCH/format/json"
    )
    key = f"{min(ids)}-{max(ids)}-n{len(ids)}-{days}d"
    cached = cached_download(
        settings.data_dir / "spacetrack" / "gp_history" / key, "json",
        min_interval_s=365 * 86400, force=False,
        download=lambda: _query(settings, path), validate=_parse,
    )
    return _parse(cached.text), cached


# --- parsing --------------------------------------------------------------

COLUMNS = [
    "cdm_id", "created", "tca", "min_rng_m", "pc", "emergency_reportable",
    "sat1_id", "sat1_name", "sat1_object_type", "sat2_id", "sat2_name", "sat2_object_type", "raw",
]

# Space-Track's field names aren't uniform (SAT_1_ID vs SAT1_OBJECT_TYPE), so
# look each one up under its known spellings.
_FIELDS = {
    "cdm_id": ("CDM_ID",),
    "created": ("CREATED",),
    "tca": ("TCA",),
    "min_rng_m": ("MIN_RNG",),
    "pc": ("PC",),
    "emergency_reportable": ("EMERGENCY_REPORTABLE",),
    "sat1_id": ("SAT_1_ID", "SAT1_ID"),
    "sat1_name": ("SAT_1_NAME", "SAT1_NAME"),
    "sat1_object_type": ("SAT1_OBJECT_TYPE", "SAT_1_OBJECT_TYPE"),
    "sat2_id": ("SAT_2_ID", "SAT2_ID"),
    "sat2_name": ("SAT_2_NAME", "SAT2_NAME"),
    "sat2_object_type": ("SAT2_OBJECT_TYPE", "SAT_2_OBJECT_TYPE"),
}


def _get(raw: dict[str, Any], col: str) -> Any:
    for key in _FIELDS[col]:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace(" ", "T").removesuffix("Z"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_cdm(raw: dict[str, Any]) -> tuple:
    flag = _get(raw, "emergency_reportable")
    return (
        int(_get(raw, "cdm_id")),
        _utc(_get(raw, "created")),
        _utc(_get(raw, "tca")),
        _opt_float(_get(raw, "min_rng_m")),
        _opt_float(_get(raw, "pc")),
        None if flag is None else str(flag).upper() in ("Y", "YES", "TRUE", "1"),
        int(_get(raw, "sat1_id")),
        _get(raw, "sat1_name"),
        _get(raw, "sat1_object_type"),
        int(_get(raw, "sat2_id")),
        _get(raw, "sat2_name"),
        _get(raw, "sat2_object_type"),
        json.dumps(raw),
    )


def parse_cdms(raws: list[dict[str, Any]]) -> tuple[list[tuple], list[tuple[Any, str]]]:
    good, bad = [], []
    for raw in raws:
        try:
            good.append(parse_cdm(raw))
        except (TypeError, ValueError) as exc:
            bad.append((raw.get("CDM_ID"), f"{type(exc).__name__}: {exc}"))
    return good, bad


# --- storage --------------------------------------------------------------

def store_cdms(conn: Connection, rows: list[tuple]) -> int:
    """Insert new messages; returns how many were new. CDMs are immutable, so existing IDs are skipped."""
    conn.execute(text(
        "CREATE TEMP TABLE cdm_stage AS SELECT " + ", ".join(COLUMNS) + " FROM cdm_public WITH NO DATA"
    ))
    copy_rows(conn, "cdm_stage", COLUMNS, rows)
    inserted = conn.execute(text(
        f"INSERT INTO cdm_public ({', '.join(COLUMNS)}) SELECT {', '.join(COLUMNS)} FROM cdm_stage "
        "ON CONFLICT (cdm_id) DO NOTHING"
    )).rowcount
    conn.execute(text("DROP TABLE cdm_stage"))
    return inserted
