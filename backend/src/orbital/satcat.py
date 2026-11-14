"""Merge CelesTrak SATCAT rows into `objects`.

SATCAT covers every catalogued object, including decayed ones, so this also
creates objects that the GP feed hasn't delivered yet.
"""

from datetime import date

from sqlalchemy import Connection, text

from orbital.ingest import copy_rows

COLUMNS = [
    "norad_cat_id", "object_name", "object_id", "object_type", "ops_status_code",
    "owner", "launch_date", "launch_site", "decay_date", "period_min",
    "inclination_deg", "apogee_km", "perigee_km", "rcs_m2", "orbit_center", "orbit_type",
]

_CSV_FIELDS = {
    "object_name": "OBJECT_NAME", "object_id": "OBJECT_ID", "object_type": "OBJECT_TYPE",
    "ops_status_code": "OPS_STATUS_CODE", "owner": "OWNER", "launch_date": "LAUNCH_DATE",
    "launch_site": "LAUNCH_SITE", "decay_date": "DECAY_DATE", "period_min": "PERIOD",
    "inclination_deg": "INCLINATION", "apogee_km": "APOGEE", "perigee_km": "PERIGEE",
    "rcs_m2": "RCS", "orbit_center": "ORBIT_CENTER", "orbit_type": "ORBIT_TYPE",
}
_DATES = {"launch_date", "decay_date"}
_FLOATS = {"period_min", "inclination_deg", "apogee_km", "perigee_km", "rcs_m2"}

_MERGE = text(f"""
    INSERT INTO objects ({", ".join(COLUMNS)}, satcat_updated_at)
    SELECT {", ".join(COLUMNS)}, now() FROM satcat_stage
    ON CONFLICT (norad_cat_id) DO UPDATE SET
        -- GP names are kept when present; SATCAT fills gaps.
        object_name = COALESCE(objects.object_name, EXCLUDED.object_name),
        object_id   = COALESCE(objects.object_id, EXCLUDED.object_id),
        {", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[3:])},
        satcat_updated_at = now(),
        updated_at = now()
    -- Only touch rows that changed, so daily reruns don't rewrite 70k rows.
    WHERE ({", ".join(f"objects.{c}" for c in COLUMNS[3:])}) IS DISTINCT FROM
          ({", ".join(f"EXCLUDED.{c}" for c in COLUMNS[3:])})
       OR objects.object_name IS NULL OR objects.object_id IS NULL
""")


def parse_row(raw: dict[str, str]) -> tuple:
    """CSV row -> tuple in COLUMNS order. Blank means unknown (or, for decay_date, still in orbit)."""
    out: list = [int(raw["NORAD_CAT_ID"])]
    for col in COLUMNS[1:]:
        value = raw.get(_CSV_FIELDS[col], "").strip()
        if not value:
            out.append(None)
        elif col in _DATES:
            out.append(date.fromisoformat(value))
        elif col in _FLOATS:
            out.append(float(value))
        else:
            out.append(value)
    return tuple(out)


def parse_rows(raws: list[dict[str, str]]) -> tuple[list[tuple], list[tuple[str, str]]]:
    good, bad = [], []
    for raw in raws:
        try:
            good.append(parse_row(raw))
        except (KeyError, ValueError) as exc:
            bad.append((raw.get("NORAD_CAT_ID", "?"), f"{type(exc).__name__}: {exc}"))
    return good, bad


def store_rows(conn: Connection, rows: list[tuple]) -> int:
    """Returns the number of objects inserted or updated."""
    conn.execute(text(
        "CREATE TEMP TABLE satcat_stage AS SELECT " + ", ".join(COLUMNS) + " FROM objects WITH NO DATA"
    ))
    copy_rows(conn, "satcat_stage", COLUMNS, rows)
    count = conn.execute(_MERGE).rowcount
    conn.execute(text("DROP TABLE satcat_stage"))
    return count
