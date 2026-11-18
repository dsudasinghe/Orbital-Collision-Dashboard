"""The deployed risk forecaster: a learned rule on the latest known risk (numpy only).

Fitted and evaluated in orbital.ml.risk_model on ESA's Kelvins data; the
parameters live in models/risk/rule.json. Risks are log10 collision probability.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orbital.config import BACKEND_DIR

HIGH_RISK = -6.0  # log10 Pc; the Kelvins challenge's notification threshold
CLIP_LOW = -6.001  # predictions below the threshold are reported here
RULE_PATH = BACKEND_DIR / "models" / "risk" / "rule.json"


@dataclass(frozen=True)
class RiskRule:
    promote_from: float = -7.3  # latest risk in [promote_from, -6) is promoted to high risk
    promoted_value: float = -5.6
    flagged_shift: float = -0.1  # applied to latest risk already >= -6

    def predict(self, latest_risk: np.ndarray) -> np.ndarray:
        latest = np.asarray(latest_risk, dtype=float)
        out = np.where(latest >= HIGH_RISK, np.maximum(latest + self.flagged_shift, -5.999), CLIP_LOW)
        return np.where((latest < HIGH_RISK) & (latest >= self.promote_from), self.promoted_value, out)


def load_rule(path: Path = RULE_PATH) -> RiskRule:
    return RiskRule(**json.loads(path.read_text())) if path.exists() else RiskRule()
