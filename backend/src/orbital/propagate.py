"""SGP4 propagation helpers.

python-sgp4 stores the satellite number in Alpha-5 form and raises for any
catalog number above 339999. The number plays no part in the orbit maths, so
we initialise every Satrec with a placeholder and keep the real integer ID
ourselves.
"""

from datetime import datetime

from sgp4.api import WGS72, Satrec, jday

from orbital.omm import OmmRecord

_SGP4_EPOCH_JD = 2433281.5  # 1949-12-31 00:00 UTC, the zero point sgp4init expects
_PLACEHOLDER_SATNUM = 0
_MINUTES_PER_DAY = 1440.0
_DEG = 3.141592653589793 / 180.0


def to_satrec(rec: OmmRecord) -> Satrec:
    jd, fr = jday(
        rec.epoch.year, rec.epoch.month, rec.epoch.day,
        rec.epoch.hour, rec.epoch.minute, rec.epoch.second + rec.epoch.microsecond / 1e6,
    )
    sat = Satrec()
    sat.sgp4init(
        WGS72,
        "i",
        _PLACEHOLDER_SATNUM,
        (jd - _SGP4_EPOCH_JD) + fr,
        rec.bstar,
        rec.mean_motion_dot / (_MINUTES_PER_DAY**2) * 2 * 3.141592653589793,
        rec.mean_motion_ddot / (_MINUTES_PER_DAY**3) * 2 * 3.141592653589793,
        rec.eccentricity,
        rec.arg_of_pericenter * _DEG,
        rec.inclination * _DEG,
        rec.mean_anomaly * _DEG,
        rec.mean_motion / _MINUTES_PER_DAY * 2 * 3.141592653589793,  # rad/min
        rec.ra_of_asc_node * _DEG,
    )
    return sat


def position_teme_km(sat: Satrec, when: datetime) -> tuple[float, float, float]:
    """TEME position in km. Raises RuntimeError with the sgp4 error code on failure."""
    jd, fr = jday(
        when.year, when.month, when.day,
        when.hour, when.minute, when.second + when.microsecond / 1e6,
    )
    err, r, _v = sat.sgp4(jd, fr)
    if err:
        raise RuntimeError(f"sgp4 error code {err}")
    return r
