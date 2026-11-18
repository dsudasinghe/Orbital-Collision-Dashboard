"""Manoeuvre and anomaly detection from element-set history.

For each pair of consecutive element sets of one object, measure what normal
evolution does not explain:

- da_resid_km: semi-major axis at the new epoch minus what the previous set's
  own mean-motion derivative predicts. Drag only shrinks an orbit, so a jump
  up is a strong manoeuvre signature (e.g. an ISS reboost).
- d_inc_deg, d_ecc: changes the previous set doesn't predict (SGP4 mean
  elements hold these nearly constant apart from slow perturbations).
- radial / in-track / cross-track position residual: SGP4-propagate the
  previous set to the new epoch and compare with the new set there. In-track
  residuals are divided by the gap in days, since they grow with it.

Each feature is then compared with the object's own history by a robust
z-score (median and MAD), so a Starlink's routine station-keeping and a
debris fragment's drag set their own baselines.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sgp4.api import jday

from orbital.omm import OmmRecord
from orbital.propagate import to_satrec

MU_KM3_S2 = 398600.4418
MAD_SCALE = 1.4826  # makes MAD consistent with the standard deviation for normal data

FEATURES = ["da_resid_km", "d_inc_deg", "d_ecc", "radial_km", "in_track_km_per_day", "cross_track_km"]


def semi_major_axis_km(mean_motion_rev_day: np.ndarray | float) -> np.ndarray:
    n = np.asarray(mean_motion_rev_day) * 2 * np.pi / 86400.0
    return np.cbrt(MU_KM3_S2 / n**2)


def _state(rec: OmmRecord, at) -> tuple[np.ndarray, np.ndarray] | None:
    sat = to_satrec(rec)
    jd, fr = jday(at.year, at.month, at.day, at.hour, at.minute, at.second + at.microsecond / 1e6)
    err, r, v = sat.sgp4(jd, fr)
    return None if err else (np.asarray(r), np.asarray(v))


def pair_features(prev: OmmRecord, new: OmmRecord) -> dict:
    dt_days = (new.epoch - prev.epoch).total_seconds() / 86400.0
    # MEAN_MOTION_DOT is half the first derivative of mean motion (rev/day^2).
    n_pred = prev.mean_motion + 2 * prev.mean_motion_dot * dt_days
    out = {
        "epoch": new.epoch,
        "dt_days": dt_days,
        "da_resid_km": float(semi_major_axis_km(new.mean_motion) - semi_major_axis_km(n_pred)),
        "d_inc_deg": new.inclination - prev.inclination,
        "d_ecc": new.eccentricity - prev.eccentricity,
        "radial_km": np.nan, "in_track_km_per_day": np.nan, "cross_track_km": np.nan,
    }
    predicted, actual = _state(prev, new.epoch), _state(new, new.epoch)
    if predicted and actual:
        r_new, v_new = actual
        u_r = r_new / np.linalg.norm(r_new)
        u_n = np.cross(r_new, v_new)
        u_n /= np.linalg.norm(u_n)
        u_t = np.cross(u_n, u_r)
        diff = r_new - predicted[0]
        out["radial_km"] = float(diff @ u_r)
        out["in_track_km_per_day"] = float(diff @ u_t) / max(dt_days, 1 / 24)
        out["cross_track_km"] = float(diff @ u_n)
    return out


def history_features(sets: list[OmmRecord]) -> pd.DataFrame:
    """Features for each consecutive pair of one object's element sets (sorted by epoch)."""
    sets = sorted(sets, key=lambda r: r.epoch)
    rows = [pair_features(a, b) for a, b in zip(sets, sets[1:]) if b.epoch > a.epoch]
    return pd.DataFrame(rows, columns=["epoch", "dt_days", *FEATURES])


def robust_z(x: pd.Series) -> pd.Series:
    med = x.median()
    mad = MAD_SCALE * (x - med).abs().median()
    return (x - med) / mad if mad > 0 else pd.Series(0.0, index=x.index)


@dataclass(frozen=True)
class RuleDetector:
    """Flag a pair when a feature is far outside the object's own history.

    Both a relative test (robust z) and an absolute floor are required, so a
    very quiet object can't flag on centimetre-level noise.
    """
    z: float = 6.0
    min_da_km: float = 0.1
    min_inc_deg: float = 0.01

    def flag(self, feats: pd.DataFrame) -> pd.DataFrame:
        out = feats.copy()
        for f in FEATURES:
            out[f"z_{f}"] = robust_z(out[f])
        raise_ = (out.z_da_resid_km >= self.z) & (out.da_resid_km >= self.min_da_km)
        lower = (out.z_da_resid_km <= -self.z) & (out.da_resid_km <= -self.min_da_km)
        plane = (out.z_d_inc_deg.abs() >= self.z) & (out.d_inc_deg.abs() >= self.min_inc_deg)
        out["kind"] = np.select([raise_, lower, plane], ["orbit raise", "orbit lower", "plane change"], default="")
        out["flag"] = out.kind != ""
        return out

    def classify(self, da_km: float, d_inc_deg: float) -> str:
        """Label a (possibly merged) event from its total change."""
        if abs(da_km) >= self.min_da_km:
            return "orbit raise" if da_km > 0 else "orbit lower"
        return "plane change"


# Two profiles, chosen by whether the object can manoeuvre. Tuned on the
# watchlist's 191 debris and rocket bodies (27,772 element-set pairs, Nov 2026)
# against confirmed ISS / Tiangong reboosts and Starlink station-keeping:
# - ACTIVE keeps small burns (Tiangong's +0.14 km on 25 Oct 2026) at the cost
#   of ~1.4 false events per 1,000 pairs had it been applied to debris;
# - INERT objects can't burn, so anything flagged there is an unexplained
#   change ("anomaly": fragmentation, data error, drag event); stricter
#   thresholds hold that to ~0.25 per 1,000 pairs.
ACTIVE = RuleDetector(z=6.0, min_da_km=0.1, min_inc_deg=0.01)
INERT = RuleDetector(z=8.0, min_da_km=0.3, min_inc_deg=0.01)
# SATCAT operational status codes for payloads that may still manoeuvre:
# operational, partially operational, backup, spare, extended mission.
ACTIVE_STATUS = {"+", "P", "B", "S", "X"}


def can_manoeuvre(object_type: str | None, ops_status_code: str | None) -> bool:
    return object_type == "PAY" and ops_status_code in ACTIVE_STATUS


# Flags this close together on one object are one manoeuvre: the orbit fits
# just after a burn keep adjusting (e.g. ISS, 26 Sep 2026: +1.97 km, then +0.11 km).
EVENT_MERGE_DAYS = 1.5
# Robust z-scores need a baseline; objects with fewer pairs aren't scored.
MIN_PAIRS = 20


@dataclass
class Manoeuvre:
    norad_cat_id: int
    epoch: object  # first element set after the burn (datetime)
    kind: str
    da_km: float
    d_inc_deg: float
    z_max: float
    pairs_merged: int


def events(norad_cat_id: int, flagged: pd.DataFrame, detector: RuleDetector = ACTIVE, inert: bool = False) -> list[Manoeuvre]:
    """Merge flagged pairs of one object into events, labelled by their total change.

    For objects that can't manoeuvre every event is labelled "anomaly".
    """
    out: list[Manoeuvre] = []
    rows = flagged[flagged.flag].sort_values("epoch")
    for _, r in rows.iterrows():
        z = float(max(abs(r.z_da_resid_km), abs(r.z_d_inc_deg)))
        last = out[-1] if out else None
        if last and (r.epoch - last.epoch).total_seconds() / 86400 <= EVENT_MERGE_DAYS:
            last.da_km += r.da_resid_km
            last.d_inc_deg += r.d_inc_deg
            last.z_max = max(last.z_max, z)
            last.pairs_merged += 1
            continue
        out.append(Manoeuvre(norad_cat_id, r.epoch, r.kind, float(r.da_resid_km), float(r.d_inc_deg), z, 1))
    for m in out:
        m.kind = "anomaly" if inert else detector.classify(m.da_km, m.d_inc_deg)
    return out


def anomaly_score(flagged: pd.DataFrame) -> float:
    """Largest robust |z| over the features of the object's latest element-set pair."""
    latest = flagged.sort_values("epoch").iloc[-1]
    zs = [abs(latest[f"z_{f}"]) for f in FEATURES if pd.notna(latest[f"z_{f}"])]
    return float(max(zs)) if zs else 0.0


def detect(histories: dict[int, list[OmmRecord]], active: set[int] | None = None) -> tuple[list[Manoeuvre], dict[int, float]]:
    """Events and latest anomaly score for every object with enough history.

    `active` lists objects that can manoeuvre (ACTIVE profile); the rest use
    the INERT profile and only produce "anomaly" events. None means all active.
    """
    manoeuvres: list[Manoeuvre] = []
    scores: dict[int, float] = {}
    for norad_cat_id, sets in histories.items():
        feats = history_features(sets)
        if len(feats) < MIN_PAIRS:
            continue
        inert = active is not None and norad_cat_id not in active
        detector = INERT if inert else ACTIVE
        flagged = detector.flag(feats)
        manoeuvres.extend(events(norad_cat_id, flagged, detector, inert))
        scores[norad_cat_id] = anomaly_score(flagged)
    return manoeuvres, scores
