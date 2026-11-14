"""Load OMM element sets into Postgres.

Every element set is kept (history drives the anomaly detector later), keyed
by (norad_cat_id, epoch). Re-ingesting the same data is a no-op.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Connection, text

from orbital.omm import OmmRecord

log = logging.getLogger(__name__)

_GP_COLUMNS = [
    "norad_cat_id", "epoch", "mean_motion", "eccentricity", "inclination",
    "ra_of_asc_node", "arg_of_pericenter", "mean_anomaly", "bstar",
    "mean_motion_dot", "mean_motion_ddot", "ephemeris_type",
    "classification_type", "element_set_no", "rev_at_epoch",
]

_OBJECT_COLUMNS = ["norad_cat_id", "object_name", "object_id"]

_CREATE_OBJECTS_STAGE = text(
    "CREATE TEMP TABLE objects_stage (norad_cat_id integer, object_name text, object_id text) ON COMMIT DROP"
)

_MERGE_OBJECTS_STAGE = text("""
    INSERT INTO objects (norad_cat_id, object_name, object_id)
    SELECT norad_cat_id, object_name, object_id FROM objects_stage
    ON CONFLICT (norad_cat_id) DO UPDATE
    SET object_name = COALESCE(EXCLUDED.object_name, objects.object_name),
        object_id   = COALESCE(EXCLUDED.object_id, objects.object_id),
        updated_at  = now()
    WHERE objects.object_name IS DISTINCT FROM EXCLUDED.object_name
       OR objects.object_id   IS DISTINCT FROM EXCLUDED.object_id
""")

_CREATE_GP_STAGE = text(
    "CREATE TEMP TABLE gp_stage (LIKE gp_elements INCLUDING DEFAULTS) ON COMMIT DROP"
)

_MERGE_GP_STAGE = text(f"""
    INSERT INTO gp_elements ({", ".join(_GP_COLUMNS)}, source)
    SELECT {", ".join(_GP_COLUMNS)}, source FROM gp_stage
    ON CONFLICT (norad_cat_id, epoch) DO NOTHING
""")


@dataclass
class IngestResult:
    received: int
    inserted: int


def month_start(dt: datetime) -> date:
    return date(dt.year, dt.month, 1)


def ensure_partitions(conn: Connection, months: set[date]) -> None:
    """gp_elements is range-partitioned by month of epoch; create any missing months."""
    for m in sorted(months):
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        name = f"gp_elements_{m:%Y_%m}"
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF gp_elements "
            f"FOR VALUES FROM ('{m.isoformat()}') TO ('{nxt.isoformat()}')"
        ))


def copy_rows(conn: Connection, table: str, columns: list[str], rows) -> None:
    """Bulk-load via COPY on the same transaction; row-by-row inserts take ~100x longer over the network."""
    with conn.connection.driver_connection.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)


def store_records(conn: Connection, records: list[OmmRecord], source: str) -> IngestResult:
    if not records:
        return IngestResult(0, 0)

    ensure_partitions(conn, {month_start(r.epoch) for r in records})

    # Latest name per object wins if a batch carries several sets for one object.
    objects = {r.norad_cat_id: r for r in sorted(records, key=lambda r: r.epoch)}
    conn.execute(_CREATE_OBJECTS_STAGE)
    copy_rows(conn, "objects_stage", _OBJECT_COLUMNS,
               ((r.norad_cat_id, r.object_name, r.object_id) for r in objects.values()))
    conn.execute(_MERGE_OBJECTS_STAGE)

    # Stage then merge, so one statement reports how many sets were new.
    conn.execute(_CREATE_GP_STAGE)
    copy_rows(conn, "gp_stage", [*_GP_COLUMNS, "source"],
               ((*(getattr(r, c) for c in _GP_COLUMNS), source) for r in records))
    inserted = conn.execute(_MERGE_GP_STAGE).rowcount
    conn.execute(text("DROP TABLE gp_stage"))
    conn.execute(text("DROP TABLE objects_stage"))
    return IngestResult(received=len(records), inserted=inserted)


def start_run(conn: Connection, source: str) -> int:
    return conn.execute(
        text("INSERT INTO ingest_runs (source) VALUES (:source) RETURNING id"), {"source": source}
    ).scalar_one()


def finish_run(
    conn: Connection, run_id: int, *, status: str, received: int = 0, inserted: int = 0,
    rejected: int = 0, from_cache: bool = False, error: str | None = None,
) -> None:
    conn.execute(text("""
        UPDATE ingest_runs
        SET finished_at = now(), status = :status, rows_received = :received,
            rows_inserted = :inserted, rows_rejected = :rejected,
            from_cache = :from_cache, error = :error
        WHERE id = :id
    """), {"id": run_id, "status": status, "received": received, "inserted": inserted,
           "rejected": rejected, "from_cache": from_cache, "error": error})


def last_attempt_age_s(conn: Connection, source: str) -> float | None:
    """Seconds since the last run that contacted the upstream service (a download, or a failure).

    Kept in the database rather than in the local file cache so the rate limit
    holds across machines, e.g. ephemeral CI runners.
    """
    age = conn.execute(text("""
        SELECT extract(epoch FROM now() - max(started_at))
        FROM ingest_runs
        WHERE source = :source AND (status = 'failed' OR (status = 'ok' AND NOT from_cache))
    """), {"source": source}).scalar()
    return None if age is None else float(age)
