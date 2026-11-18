import numpy as np
import pandas as pd
import pytest

from orbital.ml import kelvins as k
from orbital.ml.rule import CLIP_LOW, RiskRule


def test_score_matches_hand_computation():
    true = np.array([-5.0, -4.0, -7.0, -20.0])  # two high-risk events
    pred = np.array([-5.5, -8.0, -5.9, -30.0])  # TP, FN, FP, TN
    s = k.score(true, pred)
    # F2 = 5*TP / (5*TP + 4*FN + FP) = 5 / 10; MSE_HR over the two HR events,
    # with the FN's prediction clipped to -6.001.
    assert s.f2 == pytest.approx(0.5)
    assert s.mse_hr == pytest.approx(((-5.0 + 5.5) ** 2 + (-4.0 - CLIP_LOW) ** 2) / 2)
    assert s.loss == pytest.approx(s.mse_hr / s.f2)


def test_score_without_true_positives_is_infinite():
    assert k.score(np.array([-5.0]), np.array([-9.0])).loss == float("inf")


def test_prior_weight_counts_high_risk_events_more_in_f2():
    true = np.array([-5.0, -20.0])
    pred = np.array([-5.0, -5.0])  # one TP, one FP
    assert k.score(true, pred, hr_weight=9.0).f2 > k.score(true, pred).f2


def test_rule_promotes_borderline_and_nudges_flagged():
    rule = RiskRule(promote_from=-7.3, promoted_value=-5.6, flagged_shift=-0.1)
    got = rule.predict(np.array([-4.0, -6.0, -6.5, -7.3, -7.31, -30.0]))
    assert got.tolist() == pytest.approx([-4.1, -5.999, -5.6, -5.6, CLIP_LOW, CLIP_LOW])


def test_challenge_events_follow_test_set_rules():
    cdms = pd.DataFrame({
        "event_id": [1, 1, 1, 2, 2, 3, 3],
        "time_to_tca": [5.0, 2.5, 0.4, 3.0, 1.5, 1.8, 0.3],
        "risk": [-8.0, -7.0, -5.0, -6.0, -6.5, -9.0, -9.5],
    })
    inputs, target = k.challenge_events(cdms)
    # Event 1 qualifies; 2's last CDM is >= 1 day out; 3 has no CDM >= 2 days out.
    assert target.to_dict() == {1: -5.0}
    assert sorted(inputs.time_to_tca) == [2.5, 5.0]
    assert k.latest_risk(inputs).to_dict() == {1: -7.0}
