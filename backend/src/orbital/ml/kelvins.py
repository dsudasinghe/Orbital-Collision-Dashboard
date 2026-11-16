"""ESA Kelvins Collision Avoidance Challenge: data and scoring.

Dataset: Uriot et al., "Spacecraft collision avoidance challenge: design and
results of a machine learning competition", 2020 (arXiv:2008.03069). CDMs ESA
received 2015-2019, CC-BY-4.0, doi:10.5281/zenodo.4463683.

The task, as in the challenge: from an event's CDMs released at least 2 days
before TCA, predict the risk (log10 Pc) in its final CDM, released within
1 day of TCA. Events must have at least one CDM on each side.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from orbital.config import BACKEND_DIR
from orbital.ml.rule import CLIP_LOW, HIGH_RISK  # shared with the deployed rule

DATA_DIR = BACKEND_DIR / "data" / "kelvins" / "competition"

INPUT_MIN_TTCA = 2.0  # days: only CDMs at least this far before TCA are inputs
TARGET_MAX_TTCA = 1.0  # days: the final CDM must be closer than this


@dataclass
class Score:
    loss: float  # MSE_HR / F2, lower is better
    mse_hr: float
    f2: float
    n_events: int
    n_high_risk: int

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


# The test set's high-risk share is published (Uriot et al. 2020: 150 of 2167
# events); eligible training events have 66 of 8293. Weighting training
# high-risk events by the odds ratio corrects for that prior shift (label
# shift) without using test labels.
TEST_HR_SHARE = 150 / 2167
TRAIN_HR_SHARE = 66 / 8293
HR_PRIOR_WEIGHT = (TEST_HR_SHARE / (1 - TEST_HR_SHARE)) / (TRAIN_HR_SHARE / (1 - TRAIN_HR_SHARE))


def score(true_risk: np.ndarray, pred_risk: np.ndarray, hr_weight: float = 1.0) -> Score:
    """The challenge metric: MSE on true high-risk events divided by F2 over all events.

    `hr_weight` counts each true high-risk event that many times in F2, to
    estimate test-set performance from training events (see HR_PRIOR_WEIGHT).
    MSE_HR is a mean over high-risk events only, so a uniform weight leaves it unchanged.
    """
    r = np.asarray(true_risk, dtype=float)
    p = np.asarray(pred_risk, dtype=float)
    p = np.where(p < HIGH_RISK, CLIP_LOW, p)
    hr = r >= HIGH_RISK
    pred_hr = p >= HIGH_RISK
    tp = hr_weight * np.sum(hr & pred_hr)
    fp = np.sum(~hr & pred_hr)
    fn = hr_weight * np.sum(hr & ~pred_hr)
    beta2 = 4.0
    f2 = (1 + beta2) * tp / ((1 + beta2) * tp + beta2 * fn + fp) if tp else 0.0
    mse_hr = float(np.mean((r[hr] - p[hr]) ** 2)) if hr.any() else 0.0
    return Score(mse_hr / f2 if f2 else float("inf"), mse_hr, float(f2), len(r), int(hr.sum()))


def load_train(path: Path = DATA_DIR / "train_data.csv") -> pd.DataFrame:
    return pd.read_csv(path)


def load_test(path: Path = DATA_DIR / "test_data.csv", labels: Path = DATA_DIR / "test_data_private.csv") -> tuple[pd.DataFrame, pd.Series]:
    """Test inputs (CDMs >= 2 days before TCA) and the true final risk per event."""
    x = pd.read_csv(path)
    y = pd.read_csv(labels).set_index("event_id")["true_risk"]
    return x, y


def challenge_events(cdms: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Shape training events like test events: inputs and final-risk target.

    Keeps events whose last CDM is within TARGET_MAX_TTCA of TCA and that have
    at least one CDM at least INPUT_MIN_TTCA before; drops CDMs in between.
    """
    cdms = cdms.sort_values(["event_id", "time_to_tca"], ascending=[True, False])
    last = cdms.groupby("event_id").tail(1).set_index("event_id")
    early = cdms[cdms.time_to_tca >= INPUT_MIN_TTCA]
    eligible = last.index[(last.time_to_tca < TARGET_MAX_TTCA)].intersection(early.event_id.unique())
    inputs = early[early.event_id.isin(eligible)]
    target = last.loc[eligible, "risk"]
    return inputs, target


def latest_risk(inputs: pd.DataFrame) -> pd.Series:
    """Risk in each event's most recent input CDM (the LRP baseline's prediction)."""
    return inputs.sort_values(["event_id", "time_to_tca"], ascending=[True, False]).groupby("event_id").tail(1).set_index("event_id")["risk"]
