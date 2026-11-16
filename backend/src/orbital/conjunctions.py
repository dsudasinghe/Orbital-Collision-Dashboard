"""Screening runs: watchlist in, stored conjunctions and a CDM comparison out."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, text

from orbital.catalog import load_latest
from orbital.ingest import copy_rows
from orbital.screening import Encounter, ScreeningParams, collision_probability, object_radius_m, screen
from orbital.validation import load_cdm_events, match, summarise

# Objects in Space-Track public CDMs created this recently join the watchlist.
CDM_WATCH_DAYS = 7
# Encounters closer than this are stored. Space-Track's public events reach
# ~4.5 km and our miss distances differ from theirs by up to ~1 km, so 10 km
# keeps every comparable event.
STORE_THRESHOLD_KM = 10.0

_COLUMNS = [
    "run_id", "sat1_id", "sat2_id", "tca", "miss_km", "rel_speed_kms", "radial_km", "in_track_km",
    "cross_track_km", "pc_estimate", "hard_body_m", "sat1_epoch", "sat2_epoch",
]


def watch_ids(conn: Connection) -> list[int]:
    return [r[0] for r in conn.execute(text("""
        SELECT norad_cat_id FROM watchlist
        UNION
        SELECT id FROM (
            SELECT sat1_id AS id FROM cdm_public WHERE created > now() - make_interval(days => :days)
            UNION SELECT sat2_id FROM cdm_public WHERE created > now() - make_interval(days => :days)
        ) c
        ORDER BY 1
    """), {"days": CDM_WATCH_DAYS})]


def last_ok_run_age_h(conn: Connection) -> float | None:
    age = conn.execute(text(
        "SELECT extract(epoch FROM now() - max(started_at)) / 3600 FROM screening_runs WHERE status = 'ok'"
    )).scalar()
    return None if age is None else float(age)


def run(engine: Engine, params: ScreeningParams, ids: list[int] | None = None) -> dict:
    """Screen, store and validate one run. Returns a summary for printing."""
    window_end = params.start + timedelta(hours=params.hours)
    with engine.begin() as conn:
        ids = ids if ids is not None else watch_ids(conn)
        run_id = conn.execute(text("""
            INSERT INTO screening_runs (window_start, window_end, step_s, threshold_km, watch_ids)
            VALUES (:s, :e, :step, :thr, :ids) RETURNING id
        """), {"s": params.start, "e": window_end, "step": params.step_s, "thr": params.threshold_km,
               "ids": ids}).scalar_one()

    try:
        with engine.connect() as conn:
            entries = load_latest(conn)
            meta = {i: (rcs, t) for i, rcs, t in conn.execute(text("SELECT norad_cat_id, rcs_m2, object_type FROM objects"))}
        encounters, stats = screen(entries, ids, params)

        rows = []
        for e in encounters:
            if e.miss_km >= STORE_THRESHOLD_KM:
                continue
            hbr = object_radius_m(*meta.get(e.sat1_id, (None, None))) + object_radius_m(*meta.get(e.sat2_id, (None, None)))
            rows.append((run_id, e.sat1_id, e.sat2_id, e.tca, e.miss_km, e.rel_speed_kms, e.radial_km, e.in_track_km,
                         e.cross_track_km, collision_probability(e, hbr), hbr, e.sat1_epoch, e.sat2_epoch))

        with engine.begin() as conn:
            copy_rows(conn, "conjunctions", _COLUMNS, rows)
            validation = validate(conn, encounters, set(ids), {e.elements.norad_cat_id for e in entries},
                                  params.start, window_end)
            conn.execute(text("""
                UPDATE screening_runs SET finished_at = now(), status = 'ok', watch_count = :w,
                    candidate_count = :c, encounter_count = :n, timings = :t, validation = :v
                WHERE id = :id
            """), {"id": run_id, "w": stats.watch, "c": stats.shell_survivors, "n": len(rows),
                   "t": json.dumps({**{k: round(v, 1) for k, v in stats.timings_s.items()},
                                    "attached_pairs_skipped": stats.attached_skipped}),
                   "v": json.dumps(validation)})
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE screening_runs SET finished_at = now(), status = 'failed', error = :e WHERE id = :id"),
                         {"id": run_id, "e": f"{type(exc).__name__}: {exc}"})
        raise

    return {"run_id": run_id, "watch": stats.watch, "candidates": stats.shell_survivors, "stored": len(rows),
            "attached_pairs_skipped": stats.attached_skipped,
            "timings_s": {k: round(v, 1) for k, v in stats.timings_s.items()}, "validation": validation}


def validate(conn: Connection, encounters: list[Encounter], screened: set[int], catalog: set[int],
             start: datetime, end: datetime) -> dict:
    events = load_cdm_events(conn, start, end)
    return summarise(match(events, encounters), encounters, screened, catalog)


def revalidate(conn: Connection, run_id: int) -> dict:
    """Compare a stored run with the CDMs held now; Space-Track publishes events after our run."""
    run_row = conn.execute(text("SELECT window_start, window_end, watch_ids FROM screening_runs WHERE id = :id"),
                           {"id": run_id}).one()
    rows = conn.execute(text("""
        SELECT sat1_id, sat2_id, tca, miss_km, rel_speed_kms, radial_km, in_track_km, cross_track_km, sat1_epoch, sat2_epoch
        FROM conjunctions WHERE run_id = :id
    """), {"id": run_id}).all()
    encounters = [Encounter(*r) for r in rows]
    screened = set(run_row.watch_ids or [])
    catalog = {r[0] for r in conn.execute(text("SELECT DISTINCT norad_cat_id FROM gp_elements WHERE epoch > now() - interval '30 days'"))}
    result = validate(conn, encounters, screened, catalog, run_row.window_start, run_row.window_end)
    result["checked_at"] = datetime.now(UTC).isoformat()
    conn.execute(text("UPDATE screening_runs SET validation = :v WHERE id = :id"), {"id": run_id, "v": json.dumps(result)})
    return result
