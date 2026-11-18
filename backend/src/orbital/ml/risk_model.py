"""Collision-risk forecaster: predict an event's final risk from CDMs >= 2 days before TCA.

The deployed model is a learned three-parameter rule on the latest known risk:
it promotes borderline events (the F2 metric punishes missed high-risk events
four times more than false alarms) and nudges flagged values down (risk tends
to fall as TCA approaches). Parameters are chosen by cross-validation on
training events only, with high-risk events weighted to the test set's
published share (label-shift correction). LightGBM is evaluated alongside for
comparison; on this data it does not beat the latest known risk.
"""

import itertools
import json
import warnings
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sklearn.model_selection import RepeatedStratifiedKFold

from orbital.config import BACKEND_DIR
from orbital.ml import features, kelvins as k
from orbital.ml.rule import RiskRule

MODEL_DIR = BACKEND_DIR / "models" / "risk"


GRID = {
    "promote_from": (-6.3, -6.5, -6.8, -7.0, -7.3),
    "promoted_value": (-5.999, -5.8, -5.6, -5.4),
    "flagged_shift": (0.0, -0.1),
}


def _cv_summary(scores: list[k.Score]) -> dict:
    losses = np.array([s.loss for s in scores])
    return {"loss_mean": round(float(losses.mean()), 4), "loss_std": round(float(losses.std()), 4),
            "mse_hr_mean": round(float(np.mean([s.mse_hr for s in scores])), 4),
            "f2_mean": round(float(np.mean([s.f2 for s in scores])), 4)}


def fit_rule(latest: np.ndarray, final: np.ndarray, splits, hr_weight: float) -> tuple[RiskRule, list[dict]]:
    """Grid-search the rule by prior-weighted CV loss. The rule has no fitted state beyond its parameters."""
    table = []
    for values in itertools.product(*GRID.values()):
        rule = RiskRule(**dict(zip(GRID, values)))
        scores = [k.score(final[te], rule.predict(latest[te]), hr_weight) for _, te in splits]
        table.append({"params": asdict(rule), **_cv_summary(scores)})
    table.sort(key=lambda r: r["loss_mean"])
    return RiskRule(**table[0]["params"]), table


def _lightgbm_promotions(x_tr, y_tr, latest_tr, x_te, latest_te, hr_weight, tau=0.3):
    """Best LightGBM candidate from exploration: LRP flags plus classifier promotions."""
    import lightgbm as lgb

    hr = y_tr >= k.HIGH_RISK
    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=30,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                             verbose=-1, random_state=0)
    clf.fit(x_tr, hr.astype(int), sample_weight=np.where(hr, hr_weight, 1.0))
    p = clf.predict_proba(x_te[x_tr.columns])[:, 1]
    return np.where((latest_te >= k.HIGH_RISK) | (p >= tau), np.maximum(latest_te, -5.999), k.CLIP_LOW)


def evaluate(out_dir: Path = MODEL_DIR, seed: int = 1) -> dict:
    """Fit on training events by CV, then score the official test set once. Writes rule + results."""
    warnings.filterwarnings("ignore", category=UserWarning)
    w = k.HR_PRIOR_WEIGHT
    x_in, y = k.challenge_events(k.load_train())
    latest = k.latest_risk(x_in).reindex(y.index).to_numpy()
    final = y.to_numpy()
    splits = list(RepeatedStratifiedKFold(n_splits=5, n_repeats=6, random_state=seed)
                  .split(final, (final >= k.HIGH_RISK).astype(int)))

    rule, table = fit_rule(latest, final, splits, w)
    cv = {
        "lrp_baseline": _cv_summary([k.score(final[te], latest[te], w) for _, te in splits]),
        "learned_rule": {k_: v for k_, v in table[0].items() if k_ != "params"},
        "rule_runners_up": table[1:6],
    }
    x_full = features.full(x_in).reindex(y.index)
    cv["lightgbm_promotions"] = _cv_summary([
        k.score(final[te], _lightgbm_promotions(x_full.iloc[tr], final[tr], latest[tr], x_full.iloc[te], latest[te], w), w)
        for tr, te in splits
    ])

    # The official test set, scored once with the chosen models.
    x_te, y_te = k.load_test()
    latest_te = k.latest_risk(x_te).reindex(y_te.index).to_numpy()
    yt = y_te.to_numpy()
    lgbm_te = _lightgbm_promotions(x_full, final, latest, features.full(x_te).reindex(y_te.index), latest_te, w)
    test = {
        "constant_minus5": k.score(yt, np.full(len(yt), -5.0)).as_dict(),
        "lrp_baseline": k.score(yt, latest_te).as_dict(),
        "lightgbm_promotions": k.score(yt, lgbm_te).as_dict(),
        "learned_rule": k.score(yt, rule.predict(latest_te)).as_dict(),
    }
    test["learned_rule_gain_vs_lrp"] = round(1 - test["learned_rule"]["loss"] / test["lrp_baseline"]["loss"], 4)

    result = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rule": asdict(rule),
        "protocol": {
            "train_events": int(len(final)), "train_high_risk": int((final >= k.HIGH_RISK).sum()),
            "cv": "5-fold x 6 repeats, stratified on high risk, training events only",
            "hr_prior_weight": round(w, 3),
            "selection": "lowest mean prior-weighted CV loss; test scored once after selection",
        },
        "cv": cv,
        "test": test,
        "challenge_reference": {"lrp": 0.694, "winner_sesc": 0.556, "second": 0.571, "third": 0.585, "tenth": 0.649},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rule.json").write_text(json.dumps(asdict(rule), indent=2) + "\n")
    (out_dir / "results.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    return result
