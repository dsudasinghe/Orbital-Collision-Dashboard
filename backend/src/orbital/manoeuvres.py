"""Detection runs: element history in, stored manoeuvres and anomaly scores out.

Scope is the objects that matter for alerts: the screening watchlist plus
every object in the latest screening run's upcoming close approaches. A burn
by either object makes that screening's elements stale.
"""

import json
from collections import defaultdict
from dataclasses import asdict

from sqlalchemy import Engine, text

from orbital.anomaly import ACTIVE, INERT, MIN_PAIRS, can_manoeuvre, detect
from orbital.conjunctions import watch_ids
from orbital.omm import OmmRecord

HISTORY_DAYS = 90
DETECT_MIN_INTERVAL_H = 20


def scope_ids(conn) -> list[int]:
    ids = set(watch_ids(conn))
    ids |= {r[0] for r in conn.execute(text("""
        SELECT sat2_id FROM conjunctions
        WHERE run_id = (SELECT max(id) FROM screening_runs WHERE status = 'ok') AND tca > now()
    """))}
    return sorted(ids)


def load_histories(conn, ids: list[int], days: int = HISTORY_DAYS) -> dict[int, list[OmmRecord]]:
    rows = conn.execute(text("""
        SELECT * FROM gp_elements
        WHERE norad_cat_id = ANY(:ids) AND epoch > now() - make_interval(days => :days)
    """), {"ids": ids, "days": days}).mappings()
    out: dict[int, list[OmmRecord]] = defaultdict(list)
    for r in rows:
        out[r["norad_cat_id"]].append(OmmRecord(**{f: (None if f in ("object_name", "object_id") else r[f]) for f in OmmRecord.__slots__}))
    return out


def last_ok_run_age_h(conn) -> float | None:
    age = conn.execute(text(
        "SELECT extract(epoch FROM now() - max(started_at)) / 3600 FROM detection_runs WHERE status = 'ok'"
    )).scalar()
    return None if age is None else float(age)


def active_ids(conn, ids: list[int]) -> set[int]:
    rows = conn.execute(text("SELECT norad_cat_id, object_type, ops_status_code FROM objects WHERE norad_cat_id = ANY(:ids)"),
                        {"ids": ids})
    return {i for i, t, s in rows if can_manoeuvre(t, s)}


def run(engine: Engine) -> dict:
    params = {"active": asdict(ACTIVE), "inert": asdict(INERT), "history_days": HISTORY_DAYS, "min_pairs": MIN_PAIRS}
    with engine.begin() as conn:
        run_id = conn.execute(text("INSERT INTO detection_runs (params) VALUES (:p) RETURNING id"),
                              {"p": json.dumps(params)}).scalar_one()
    try:
        with engine.connect() as conn:
            ids = scope_ids(conn)
            histories = load_histories(conn, ids)
            active = active_ids(conn, ids)
        found, scores = detect(histories, active)
        with engine.begin() as conn:
            if found:
                conn.execute(text("""
                    INSERT INTO manoeuvres (norad_cat_id, epoch, kind, da_km, d_inc_deg, z_max, pairs_merged, run_id)
                    VALUES (:norad_cat_id, :epoch, :kind, :da_km, :d_inc_deg, :z_max, :pairs_merged, :run_id)
                    ON CONFLICT (norad_cat_id, epoch) DO UPDATE SET
                        kind = EXCLUDED.kind, da_km = EXCLUDED.da_km, d_inc_deg = EXCLUDED.d_inc_deg,
                        z_max = EXCLUDED.z_max, pairs_merged = EXCLUDED.pairs_merged, run_id = EXCLUDED.run_id
                """), [{**asdict(m), "run_id": run_id} for m in found])
            if scores:
                conn.execute(text("""
                    INSERT INTO anomaly_scores (norad_cat_id, score, run_id) VALUES (:id, :score, :run_id)
                    ON CONFLICT (norad_cat_id) DO UPDATE SET score = EXCLUDED.score, scored_at = now(), run_id = EXCLUDED.run_id
                """), [{"id": i, "score": s, "run_id": run_id} for i, s in scores.items()])
            conn.execute(text("""
                UPDATE detection_runs SET finished_at = now(), status = 'ok', objects_scored = :n, manoeuvres = :m
                WHERE id = :id
            """), {"id": run_id, "n": len(scores), "m": len(found)})
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE detection_runs SET finished_at = now(), status = 'failed', error = :e WHERE id = :id"),
                         {"id": run_id, "e": f"{type(exc).__name__}: {exc}"})
        raise
    return {"run_id": run_id, "objects_in_scope": len(ids), "objects_with_history": len(histories),
            "objects_scored": len(scores), "manoeuvres": len(found)}
