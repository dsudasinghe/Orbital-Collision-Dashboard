"""Reference-frame conversions for SGP4 output.

SGP4 returns positions in TEME (True Equator, Mean Equinox). For ground tracks
and comparison with trackers we rotate to Earth-fixed (ECEF) by Greenwich mean
sidereal time and convert to WGS-84 geodetic coordinates. Polar motion and the
UT1-UTC difference are ignored: both are well under 100 m here, far below
SGP4's own error of roughly 1 km at epoch.
"""

import numpy as np

WGS84_A_KM = 6378.137
WGS84_F = 1 / 298.257223563
_E2 = WGS84_F * (2 - WGS84_F)


def gmst_rad(jd_ut1: np.ndarray | float, fr: np.ndarray | float = 0.0) -> np.ndarray:
    """Greenwich mean sidereal time, IAU-82 (the model SGP4/TEME is defined with)."""
    t = ((np.asarray(jd_ut1) - 2451545.0) + np.asarray(fr)) / 36525.0
    seconds = (
        67310.54841
        + (876600.0 * 3600 + 8640184.812866) * t
        + 0.093104 * t**2
        - 6.2e-6 * t**3
    )
    return np.mod(np.deg2rad(seconds / 240.0), 2 * np.pi)


def teme_to_ecef(r_teme: np.ndarray, gmst: np.ndarray | float) -> np.ndarray:
    """Rotate (..., 3) TEME positions about z by GMST. `gmst` broadcasts over the leading axes."""
    r = np.asarray(r_teme, dtype=float)
    c, s = np.cos(gmst), np.sin(gmst)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    return np.stack([c * x + s * y, -s * x + c * y, z], axis=-1)


def ecef_to_geodetic(r_ecef: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """WGS-84 latitude and longitude (degrees) and height (km) from (..., 3) ECEF km."""
    r = np.asarray(r_ecef, dtype=float)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)

    def height(lat, n):
        # p/cos(lat) is unstable near the poles; switch to the z-based form there.
        polar = np.abs(lat) > np.deg2rad(45)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(polar, z / np.sin(lat) - n * (1 - _E2), p / np.cos(lat) - n)

    lat = np.arctan2(z, p * (1 - _E2))
    for _ in range(5):  # converges to sub-millimetre in 3-4 iterations for LEO-GEO
        n = WGS84_A_KM / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
        lat = np.arctan2(z, p * (1 - _E2 * n / (n + height(lat, n))))
    n = WGS84_A_KM / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
    return np.rad2deg(lat), np.rad2deg(lon), height(lat, n)
