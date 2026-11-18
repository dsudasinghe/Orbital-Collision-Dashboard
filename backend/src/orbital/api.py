"""HTTP API for the dashboard.

The globe propagates orbits in the browser (satellite.js), so the main payload
is the latest OMM element set per object, sent once and cached. Raw Space-Track
CDMs are never exposed here: the user agreement restricts redistribution.
"""

import gzip
import hashlib
import json
import math
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from orbital.catalog import load_latest
from orbital.db import get_engine
from orbital.ml.rule import HIGH_RISK, load_rule

_RISK_RULE = load_rule()
CATALOG_TTL_S = 10 * 60  # data refreshes every 2 h; 10 min keeps the API close behind

# Column order of /api/catalog rows. OMM keyword names, so the browser can hand
# each row straight to satellite.js json2satrec.
CATALOG_FIELDS = [
    "NORAD_CAT_ID", "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY",
    "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR",
    "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT", "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE",
    "ELEMENT_SET_NO", "REV_AT_EPOCH", "OBJECT_TYPE", "OWNER",
]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Build the catalog in the background so the first visitor doesn't wait for it.
    threading.Thread(target=_get_catalog, daemon=True).start()
    yield


app = FastAPI(title="Orbital Collision-Risk Dashboard API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@dataclass
class _CatalogPayload:
    gz: bytes
    etag: str
    built_at: float


_catalog: _CatalogPayload | None = None
_catalog_lock = threading.Lock()


def _build_catalog() -> _CatalogPayload:
    with get_engine().connect() as conn:
        entries = load_latest(conn)
    rows = []
    for e in entries:
        r = e.elements
        rows.append([
            r.norad_cat_id, r.object_name, r.object_id,
            r.epoch.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            r.mean_motion, r.eccentricity, r.inclination, r.ra_of_asc_node,
            r.arg_of_pericenter, r.mean_anomaly, r.bstar, r.mean_motion_dot,
            r.mean_motion_ddot, r.ephemeris_type, r.classification_type,
            r.element_set_no, r.rev_at_epoch, e.object_type, e.owner,
        ])
    body = json.dumps(
        {"generated_at": datetime.now(UTC).isoformat(), "count": len(rows), "fields": CATALOG_FIELDS, "rows": rows},
        separators=(",", ":"),
    ).encode()
    return _CatalogPayload(gzip.compress(body, 6), f'"{hashlib.sha256(body).hexdigest()[:16]}"', time.monotonic())


def _get_catalog() -> _CatalogPayload:
    global _catalog
    with _catalog_lock:
        if _catalog is None or time.monotonic() - _catalog.built_at > CATALOG_TTL_S:
            _catalog = _build_catalog()
        return _catalog


@app.get("/api/health")
def health() -> dict:
    with get_engine().connect() as conn:
        last = conn.execute(text("""
            SELECT source, max(finished_at) AS finished_at
            FROM ingest_runs WHERE status = 'ok' GROUP BY source ORDER BY source
        """)).all()
    return {"ok": True, "last_successful_run": {s: f.isoformat() if f else None for s, f in last}}


@app.get("/api/catalog")
def catalog(request: Request) -> Response:
    """Latest element set for every object in orbit, as columnar JSON (gzip, about 2 MB)."""
    payload = _get_catalog()
    headers = {"ETag": payload.etag, "Cache-Control": "public, max-age=600"}
    if request.headers.get("if-none-match") == payload.etag:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(payload.gz, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(gzip.decompress(payload.gz), media_type="application/json", headers=headers)


@app.get("/api/objects/{norad_cat_id}")
def object_detail(norad_cat_id: int) -> dict:
    """SATCAT details, element-set history summary and conjunction counts for one object."""
    with get_engine().connect() as conn:
        obj = conn.execute(text("""
            SELECT norad_cat_id, object_name, object_id, object_type, ops_status_code, owner,
                   launch_date, launch_site, decay_date, period_min, inclination_deg,
                   apogee_km, perigee_km, rcs_m2, orbit_type
            FROM objects WHERE norad_cat_id = :id
        """), {"id": norad_cat_id}).mappings().first()
        if obj is None:
            raise HTTPException(404, "unknown catalog number")
        history = conn.execute(text("""
            SELECT epoch, mean_motion, eccentricity, inclination, bstar
            FROM gp_elements WHERE norad_cat_id = :id
            ORDER BY epoch DESC LIMIT 50
        """), {"id": norad_cat_id}).mappings().all()
        # Derived counts only; raw CDMs stay private.
        conj = conn.execute(text("""
            SELECT count(*) AS events, min(min_rng_m) AS closest_m, max(pc) AS max_pc,
                   min(tca) FILTER (WHERE tca > now()) AS next_tca
            FROM cdm_public WHERE :id IN (sat1_id, sat2_id)
        """), {"id": norad_cat_id}).mappings().one()
        events = conn.execute(text("""
            SELECT epoch, kind, da_km, d_inc_deg FROM manoeuvres
            WHERE norad_cat_id = :id AND epoch > now() - interval '90 days'
            ORDER BY epoch DESC
        """), {"id": norad_cat_id}).mappings().all()
        score = conn.execute(text("SELECT score, scored_at FROM anomaly_scores WHERE norad_cat_id = :id"),
                             {"id": norad_cat_id}).mappings().first()
    return {"object": dict(obj), "elements": [dict(h) for h in history], "conjunctions": dict(conj),
            "manoeuvres": [dict(e) for e in events], "anomaly": dict(score) if score else None}


@app.get("/api/conjunctions")
def upcoming_conjunctions(limit: int = 50, max_km: float = 5.0) -> dict:
    """Upcoming close approaches from the latest screening run, closest first.

    Results are our own SGP4 screening. `in_spacetrack` says whether Space-Track
    published a public CDM for the same pair near the same TCA; the message
    itself is never returned.
    """
    limit = max(1, min(limit, 500))
    with get_engine().connect() as conn:
        run = conn.execute(text("""
            SELECT id, finished_at, window_start, window_end, watch_count, validation
            FROM screening_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1
        """)).mappings().first()
        if run is None:
            return {"run": None, "events": []}
        rows = conn.execute(text("""
            SELECT c.sat1_id, o1.object_name AS sat1_name, o1.object_type AS sat1_type,
                   c.sat2_id, o2.object_name AS sat2_name, o2.object_type AS sat2_type,
                   c.tca, c.miss_km, c.rel_speed_kms, c.radial_km, c.in_track_km, c.cross_track_km,
                   c.pc_estimate,
                   st.created AS st_created, st.pc AS st_pc,
                   m1.kind AS sat1_event, m2.kind AS sat2_event
            FROM conjunctions c
            JOIN objects o1 ON o1.norad_cat_id = c.sat1_id
            JOIN objects o2 ON o2.norad_cat_id = c.sat2_id
            -- Latest Space-Track public CDM for the same pair near the same TCA, if any.
            LEFT JOIN LATERAL (
                SELECT p.created, p.pc FROM cdm_public p
                WHERE LEAST(p.sat1_id, p.sat2_id) = LEAST(c.sat1_id, c.sat2_id)
                  AND GREATEST(p.sat1_id, p.sat2_id) = GREATEST(c.sat1_id, c.sat2_id)
                  AND p.tca BETWEEN c.tca - interval '2 minutes' AND c.tca + interval '2 minutes'
                ORDER BY p.created DESC LIMIT 1
            ) st ON true
            -- A manoeuvre or anomaly newer than the elements this screening used
            -- makes its miss distance stale.
            LEFT JOIN LATERAL (
                SELECT kind FROM manoeuvres m WHERE m.norad_cat_id = c.sat1_id AND m.epoch > c.sat1_epoch
                ORDER BY m.epoch DESC LIMIT 1
            ) m1 ON true
            LEFT JOIN LATERAL (
                SELECT kind FROM manoeuvres m WHERE m.norad_cat_id = c.sat2_id AND m.epoch > c.sat2_epoch
                ORDER BY m.epoch DESC LIMIT 1
            ) m2 ON true
            WHERE c.run_id = :run AND c.tca > now() AND c.miss_km < :max_km
            ORDER BY c.miss_km
            LIMIT :limit
        """), {"run": run["id"], "max_km": max_km, "limit": limit}).mappings().all()
    return {"run": dict(run), "events": [_with_forecast(dict(r)) for r in rows]}


def _with_forecast(row: dict) -> dict:
    """Replace the Space-Track fields with derived ones: never the message or its Pc.

    `forecast_high_risk` is the risk forecaster (models/risk/MODEL_CARD.md)
    applied to Space-Track's latest Pc: will the final CDM's risk be >= 1e-6?
    Experimental: the model was trained on ESA CDMs released >= 2 days before
    TCA; `forecast_lead_days` says how far ahead this one is.
    """
    row["stale"] = bool(row["sat1_event"] or row["sat2_event"])
    created, pc = row.pop("st_created"), row.pop("st_pc")
    row["in_spacetrack"] = created is not None
    row["forecast_high_risk"] = None
    row["forecast_lead_days"] = None
    if created is not None and pc is not None:
        latest = math.log10(pc) if pc > 0 else -30.0
        row["forecast_high_risk"] = bool(_RISK_RULE.predict([latest])[0] >= HIGH_RISK)
        row["forecast_lead_days"] = round((row["tca"] - created).total_seconds() / 86400, 1)
    return row
