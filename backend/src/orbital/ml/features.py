"""Per-event features from an event's input CDMs (those >= 2 days before TCA).

Two feature sets:

- full: the latest CDM's 100-odd ESA fields plus history features. Only
  available for ESA's own CDMs (the Kelvins dataset).
- lite: only what Space-Track's public CDMs also carry (risk, miss distance,
  time to TCA, chaser object type, message history), so the same model can
  score live public events.
"""

import numpy as np
import pandas as pd

OBJECT_TYPES = ["DEBRIS", "PAYLOAD", "ROCKET BODY", "UNKNOWN"]
LITE_LATEST = ["risk", "miss_distance", "time_to_tca"]
FLOOR = -30.0  # ESA reports risks below 1e-30 as -30


def _slope(t: np.ndarray, y: np.ndarray) -> float:
    """Least-squares slope of y against days to TCA, per day closer to TCA."""
    if len(t) < 2 or np.ptp(t) == 0:
        return 0.0
    return float(-np.polyfit(t, y, 1)[0])


def history(inputs: pd.DataFrame) -> pd.DataFrame:
    """Trend features over each event's input CDMs."""
    rows = {}
    for event_id, g in inputs.sort_values("time_to_tca", ascending=False).groupby("event_id", sort=False):
        t = g.time_to_tca.to_numpy()
        risk = g.risk.to_numpy()
        miss = np.log10(np.maximum(g.miss_distance.to_numpy(), 1.0))
        rows[event_id] = {
            "n_cdms": len(g),
            "span_days": float(t[0] - t[-1]),
            "risk_first": risk[0],
            "risk_max": risk.max(),
            "risk_min": risk.min(),
            "risk_mean": risk.mean(),
            "risk_std": risk.std() if len(risk) > 1 else 0.0,
            "risk_delta_last": risk[-1] - risk[-2] if len(risk) > 1 else 0.0,
            "risk_slope": _slope(t, risk),
            "risk_slope_last3": _slope(t[-3:], risk[-3:]),
            "risk_at_floor_share": float(np.mean(risk <= FLOOR)),
            "log_miss_first": miss[0],
            "log_miss_min": miss.min(),
            "log_miss_delta_last": miss[-1] - miss[-2] if len(miss) > 1 else 0.0,
            "log_miss_slope": _slope(t, miss),
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def _latest(inputs: pd.DataFrame) -> pd.DataFrame:
    return inputs.sort_values(["event_id", "time_to_tca"], ascending=[True, False]).groupby("event_id").tail(1).set_index("event_id")


def _object_type_dummies(latest: pd.DataFrame) -> pd.DataFrame:
    t = latest["c_object_type"].where(latest["c_object_type"].isin(OBJECT_TYPES), "UNKNOWN")
    return pd.DataFrame({f"chaser_{o.lower().replace(' ', '_')}": (t == o).astype(float) for o in OBJECT_TYPES}, index=latest.index)


def lite(inputs: pd.DataFrame) -> pd.DataFrame:
    latest = _latest(inputs)
    out = latest[LITE_LATEST].copy()
    out["log_miss"] = np.log10(np.maximum(out.pop("miss_distance"), 1.0))
    return out.join(_object_type_dummies(latest)).join(history(inputs))


def full(inputs: pd.DataFrame) -> pd.DataFrame:
    latest = _latest(inputs)
    # mission_id is left out: missions are unevenly split between train and test
    # (Uriot et al. 2020, fig. 8), so it invites learning mission identity.
    numeric = latest.drop(columns=["c_object_type", "mission_id"]).select_dtypes("number")
    numeric = numeric.rename(columns={c: f"latest_{c}" for c in numeric.columns})
    return numeric.join(_object_type_dummies(latest)).join(history(inputs))


FEATURE_SETS = {"lite": lite, "full": full}
