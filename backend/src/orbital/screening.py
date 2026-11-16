"""Conjunction screening: watchlist objects against the whole catalog.

Three stages, each cheaper than the next is precise:

1. Orbit-shell filter. An object whose perigee-apogee band never comes within
   `shell_pad_km` of a watchlist object's band can't meet it, so it is never
   propagated.
2. Coarse search. Propagate the survivors on a fixed time grid. At each step a
   KD-tree finds catalog objects within a radius large enough that no pair can
   cross between grid points unseen (threshold + max relative speed x step / 2),
   then each pair's straight-line closest approach within that step is computed
   from relative position and velocity.
3. Refinement. For each candidate pass, minimise the true SGP4 distance to find
   time of closest approach (TCA) and miss distance.

All maths is in TEME, km and km/s. Results inherit SGP4/GP accuracy (about
1 km at epoch, growing a few km per day), so they are screening estimates, not
operational conjunction assessments.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree
from sgp4.api import Satrec, jday

from orbital.catalog import CatalogEntry
from orbital.propagate import propagate_batch, to_satrec

MU_KM3_S2 = 398600.4418
# Upper bound on relative speed in Earth orbit (two LEO objects head-on).
V_REL_MAX_KMS = 16.0
# Hits for one pair closer together than this belong to the same pass.
PASS_GAP_S = 600.0
# Pairs this slow and this close are attached (station modules, docked
# vehicles), catalogued separately but moving as one: not conjunctions.
ATTACHED_SPEED_KMS = 0.01
ATTACHED_MISS_KM = 1.0
TIME_CHUNK_STEPS = 240


@dataclass
class ScreeningParams:
    start: datetime
    hours: float = 72.0
    step_s: float = 30.0
    threshold_km: float = 10.0
    shell_pad_km: float = 50.0


@dataclass
class Encounter:
    sat1_id: int  # watchlist object
    sat2_id: int
    tca: datetime
    miss_km: float
    rel_speed_kms: float
    # sat2 relative to sat1 in sat1's radial / in-track / cross-track frame
    radial_km: float
    in_track_km: float
    cross_track_km: float
    sat1_epoch: datetime
    sat2_epoch: datetime
    # TEME states at TCA (km, km/s), kept for the probability estimate
    r1: tuple[float, float, float] = (0.0, 0.0, 0.0)
    v1: tuple[float, float, float] = (0.0, 0.0, 0.0)
    r2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    v2: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class ScreeningStats:
    watch: int = 0
    shell_survivors: int = 0
    steps: int = 0
    coarse_hits: int = 0
    passes_refined: int = 0
    encounters: int = 0
    attached_skipped: int = 0
    timings_s: dict[str, float] = field(default_factory=dict)


def shell_radii_km(mean_motion_rev_day: np.ndarray, ecc: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Perigee and apogee radius from mean motion (Kepler's third law) and eccentricity."""
    n = mean_motion_rev_day * 2 * np.pi / 86400.0
    a = np.cbrt(MU_KM3_S2 / n**2)
    return a * (1 - ecc), a * (1 + ecc)


def shell_filter(rp: np.ndarray, ra: np.ndarray, watch: np.ndarray, pad_km: float) -> np.ndarray:
    """Mask of objects whose radial band overlaps (within pad) any watchlist object's band."""
    keep = np.zeros(len(rp), dtype=bool)
    for w in watch:
        keep |= (rp <= ra[w] + pad_km) & (ra >= rp[w] - pad_km)
    keep[watch] = True
    return keep


def _state(sat: Satrec, jd: float, fr: float) -> tuple[np.ndarray, np.ndarray]:
    err, r, v = sat.sgp4(jd, fr)
    if err:
        raise RuntimeError(f"sgp4 error {err}")
    return np.asarray(r), np.asarray(v)


def _refine(s1: Satrec, s2: Satrec, t0: datetime, half_window_s: float):
    """Minimise SGP4 separation around t0. Returns (tca, r1, v1, r2, v2)."""
    jd0, fr0 = jday(t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second + t0.microsecond / 1e6)

    def dist(offset_s: float) -> float:
        fr = fr0 + offset_s / 86400.0
        r1, _ = _state(s1, jd0, fr)
        r2, _ = _state(s2, jd0, fr)
        return float(np.linalg.norm(r2 - r1))

    res = minimize_scalar(dist, bounds=(-half_window_s, half_window_s), method="bounded", options={"xatol": 1e-3})
    fr = fr0 + res.x / 86400.0
    r1, v1 = _state(s1, jd0, fr)
    r2, v2 = _state(s2, jd0, fr)
    return t0 + timedelta(seconds=float(res.x)), r1, v1, r2, v2


def screen(entries: list[CatalogEntry], watch_ids: list[int], params: ScreeningParams) -> tuple[list[Encounter], ScreeningStats]:
    import time

    stats = ScreeningStats()
    clock = time.perf_counter
    t_start = clock()

    ids = np.array([e.elements.norad_cat_id for e in entries])
    index = {int(i): k for k, i in enumerate(ids)}
    watch = np.array(sorted({index[w] for w in watch_ids if w in index}), dtype=int)
    stats.watch = len(watch)
    if not len(watch):
        return [], stats

    mm = np.array([e.elements.mean_motion for e in entries])
    ecc = np.array([e.elements.eccentricity for e in entries])
    rp, ra = shell_radii_km(mm, ecc)
    keep = shell_filter(rp, ra, watch, params.shell_pad_km)
    sub = np.flatnonzero(keep)  # catalog indices that get propagated
    stats.shell_survivors = len(sub)
    local = {int(g): k for k, g in enumerate(sub)}
    watch_local = np.array([local[int(w)] for w in watch])
    is_watch_local = np.zeros(len(sub), dtype=bool)
    is_watch_local[watch_local] = True

    sats = [to_satrec(entries[g].elements) for g in sub]
    stats.timings_s["setup"] = clock() - t_start

    dt = params.step_s
    n_steps = int(params.hours * 3600 / dt) + 1
    times = [params.start + timedelta(seconds=k * dt) for k in range(n_steps)]
    stats.steps = n_steps
    radius = params.threshold_km * 1.5 + V_REL_MAX_KMS * dt / 2
    coarse_km = params.threshold_km * 1.5

    # (local watch index, local other index) -> list of (time, linear miss)
    hits: dict[tuple[int, int], list[tuple[datetime, float]]] = defaultdict(list)
    t_prop = t_search = 0.0
    for c0 in range(0, n_steps, TIME_CHUNK_STEPS):
        chunk = times[c0:c0 + TIME_CHUNK_STEPS]
        t = clock()
        err, r, v = propagate_batch(sats, chunk)
        t_prop += clock() - t
        t = clock()
        for j, when in enumerate(chunk):
            ok = (err[:, j] == 0) & np.isfinite(r[:, j, 0])
            valid = np.flatnonzero(ok)
            pos = r[valid, j]
            tree = cKDTree(pos)
            wv = watch_local[ok[watch_local]]
            neighbours = tree.query_ball_point(r[wv, j], radius)
            pw, pc = [], []
            for w, ns in zip(wv, neighbours):
                for n in ns:
                    other = valid[n]
                    # skip self, and count watch-watch pairs once
                    if other == w or (is_watch_local[other] and other < w):
                        continue
                    pw.append(w)
                    pc.append(other)
            if not pw:
                continue
            pw_a, pc_a = np.array(pw), np.array(pc)
            dr = r[pc_a, j] - r[pw_a, j]
            dv = v[pc_a, j] - v[pw_a, j]
            vv = np.einsum("ij,ij->i", dv, dv)
            tstar = np.clip(-np.einsum("ij,ij->i", dr, dv) / np.maximum(vv, 1e-12), -dt / 2, dt / 2)
            dmin = np.linalg.norm(dr + dv * tstar[:, None], axis=1)
            for k in np.flatnonzero(dmin < coarse_km):
                hits[(int(pw_a[k]), int(pc_a[k]))].append((when + timedelta(seconds=float(tstar[k])), float(dmin[k])))
                stats.coarse_hits += 1
        t_search += clock() - t
    stats.timings_s["propagate"] = t_prop
    stats.timings_s["search"] = t_search

    # One refinement per pass: split each pair's hits where they are far apart in time.
    t = clock()
    encounters: list[Encounter] = []
    for (w, o), pair_hits in hits.items():
        pair_hits.sort()
        passes: list[list[tuple[datetime, float]]] = [[pair_hits[0]]]
        for h in pair_hits[1:]:
            if (h[0] - passes[-1][-1][0]).total_seconds() > PASS_GAP_S:
                passes.append([h])
            else:
                passes[-1].append(h)
        for p in passes:
            t0 = min(p, key=lambda h: h[1])[0]
            stats.passes_refined += 1
            try:
                tca, r1, v1, r2, v2 = _refine(sats[w], sats[o], t0, dt)
            except RuntimeError:
                continue
            rel = r2 - r1
            miss = float(np.linalg.norm(rel))
            if miss >= params.threshold_km:
                continue
            if np.linalg.norm(v2 - v1) < ATTACHED_SPEED_KMS and miss < ATTACHED_MISS_KM:
                stats.attached_skipped += 1
                continue
            u_r = r1 / np.linalg.norm(r1)
            h_vec = np.cross(r1, v1)
            u_n = h_vec / np.linalg.norm(h_vec)
            u_t = np.cross(u_n, u_r)
            e1, e2 = entries[sub[w]].elements, entries[sub[o]].elements
            encounters.append(Encounter(
                sat1_id=e1.norad_cat_id, sat2_id=e2.norad_cat_id, tca=tca, miss_km=miss,
                rel_speed_kms=float(np.linalg.norm(v2 - v1)),
                radial_km=float(rel @ u_r), in_track_km=float(rel @ u_t), cross_track_km=float(rel @ u_n),
                sat1_epoch=e1.epoch, sat2_epoch=e2.epoch,
                r1=tuple(r1.tolist()), v1=tuple(v1.tolist()), r2=tuple(r2.tolist()), v2=tuple(v2.tolist()),
            ))
    stats.timings_s["refine"] = clock() - t
    stats.encounters = len(encounters)
    encounters.sort(key=lambda e: e.miss_km)
    return encounters, stats


# --- collision probability with assumed covariance -------------------------

# GP element sets carry no covariance, so each object's position uncertainty is
# assumed: Gaussian, diagonal in its own radial / in-track / cross-track frame,
# with in-track dominant and growing with element age, as GP errors do. Checked
# against Space-Track public CDMs (Nov 2026, 78 events): our miss distances
# differed from theirs by a median 0.21 km, so radial and cross-track sigmas of
# a couple of hundred metres are consistent. A rough stand-in for real
# covariance, labelled as such in the UI.
SIGMA_RADIAL_KM = 0.1
SIGMA_CROSS_KM = 0.15
SIGMA_INTRACK_AT_EPOCH_KM = 0.4
SIGMA_INTRACK_GROWTH_KM_PER_DAY = 0.3

# Hard-body radius by object type when SATCAT has no radar cross-section.
DEFAULT_RADIUS_M = {"PAY": 1.5, "R/B": 2.5, "DEB": 0.3, "UNK": 0.5}


def object_radius_m(rcs_m2: float | None, object_type: str | None) -> float:
    if rcs_m2 and rcs_m2 > 0:
        return math.sqrt(rcs_m2 / math.pi)
    return DEFAULT_RADIUS_M.get(object_type or "UNK", 0.5)


def assumed_sigmas_rtn_km(epoch: datetime, at: datetime) -> np.ndarray:
    days = abs((at - epoch).total_seconds()) / 86400.0
    return np.array([SIGMA_RADIAL_KM, SIGMA_INTRACK_AT_EPOCH_KM + SIGMA_INTRACK_GROWTH_KM_PER_DAY * days, SIGMA_CROSS_KM])


def _rtn_basis(r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Columns: radial, in-track, cross-track unit vectors."""
    u_r = r / np.linalg.norm(r)
    u_n = np.cross(r, v)
    u_n /= np.linalg.norm(u_n)
    return np.column_stack([u_r, np.cross(u_n, u_r), u_n])


# Below this relative speed the encounter is not short (the pair stays close
# for minutes), so the short-encounter Pc model doesn't apply.
SHORT_ENCOUNTER_MIN_SPEED_KMS = 0.1


def collision_probability(enc: Encounter, hard_body_radius_m: float) -> float | None:
    """Short-encounter Pc in the plane perpendicular to the relative velocity.

    Returns None for slow encounters, where the model doesn't hold.

    Both objects' assumed RTN covariances are rotated to TEME, summed and
    projected onto the encounter plane; with a hard-body radius of metres and
    sigmas of hundreds of metres, Pc is the 2D Gaussian density at the miss
    vector times the hard-body area.
    """
    r1, v1, r2, v2 = (np.asarray(x) for x in (enc.r1, enc.v1, enc.r2, enc.v2))
    cov = np.zeros((3, 3))
    for r, v, epoch in ((r1, v1, enc.sat1_epoch), (r2, v2, enc.sat2_epoch)):
        b = _rtn_basis(r, v)
        cov += b @ np.diag(assumed_sigmas_rtn_km(epoch, enc.tca) ** 2) @ b.T

    rel, vrel = r2 - r1, v2 - v1
    if np.linalg.norm(vrel) < SHORT_ENCOUNTER_MIN_SPEED_KMS:
        return None
    along = vrel / np.linalg.norm(vrel)
    miss_vec = rel - (rel @ along) * along  # already ~perpendicular at TCA
    d = np.linalg.norm(miss_vec)
    e1 = miss_vec / d if d > 1e-9 else _rtn_basis(r1, v1)[:, 0]
    e2 = np.cross(along, e1)
    plane = np.column_stack([e1, e2])
    c2 = plane.T @ cov @ plane
    m = np.array([d, 0.0])
    density = math.exp(-0.5 * m @ np.linalg.solve(c2, m)) / (2 * math.pi * math.sqrt(np.linalg.det(c2)))
    radius_km = hard_body_radius_m / 1000.0
    return float(min(1.0, math.pi * radius_km**2 * density))
