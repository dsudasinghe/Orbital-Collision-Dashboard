# Backend

Python service for ingest, propagation, conjunction screening and ML. Data lives in Supabase Postgres.

## Setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"   # [dev] includes the ML extra; the API alone needs only `pip install -e .`
copy .env.example .env   # then fill in DATABASE_URL
```

`DATABASE_URL` is the **Session pooler** string from Supabase (Project → Connect). It works over IPv4; the direct connection is IPv6-only on free projects.

## Commands

```powershell
.venv\Scripts\orbital db-check              # test the connection
.venv\Scripts\alembic upgrade head          # create / update tables
.venv\Scripts\orbital refresh               # every feed that is due (what the scheduler runs)
.venv\Scripts\orbital ingest-gp             # CelesTrak GROUP=active (operational payloads), OMM JSON
.venv\Scripts\orbital ingest-satcat         # CelesTrak SATCAT: type, owner, launch/decay dates
.venv\Scripts\orbital ingest-catalog        # Space-Track full GP catalog, debris included
.venv\Scripts\orbital ingest-cdm --days 3   # Space-Track public conjunction messages
.venv\Scripts\orbital screen                 # screen the watchlist against the catalog, next 72 h (daily)
.venv\Scripts\orbital validate              # re-compare the latest screening run with Space-Track CDMs
.venv\Scripts\orbital watchlist add 25544 --label ISS
.venv\Scripts\orbital ml-risk               # fit + evaluate the risk forecaster on ESA Kelvins data (~20 s)
.venv\Scripts\python -m pytest              # tests (no database needed)
.venv\Scripts\python -m uvicorn orbital.api:app --reload --port 8000   # API at http://localhost:8000/docs
```

## API

- `GET /api/catalog`: latest OMM element set for every object in orbit (about 33k), columnar JSON with OMM keyword names so the browser can pass each row to satellite.js `json2satrec`. About 1.9 MB gzipped; cached in memory for 10 minutes, with an ETag.
- `GET /api/objects/{norad_cat_id}`: SATCAT details, recent element sets and conjunction counts. Raw Space-Track CDMs are never served.
- `GET /api/conjunctions`: upcoming close approaches from the latest screening run, closest first, with an `in_spacetrack` flag (whether Space-Track published the same event; the message itself is never returned) and the run's validation summary.
- `GET /api/health`: last successful run per feed.

Propagation for the globe happens in the browser. satellite.js 7.1.0 and python-sgp4 agree to the millimetre on the test fixtures, including catalog numbers 100000 and 123456789.

## Feeds and rate limits

| Source | Feed | Minimum interval | Provider limit |
| --- | --- | --- | --- |
| `celestrak:active` | GP, active payloads | 2 h | don't refetch within the ~2 h update cycle |
| `celestrak:satcat` | SATCAT CSV | 24 h | regenerated daily |
| `spacetrack:gp` | GP, full catalog | 8 h | 1/hour |
| `spacetrack:cdm_public` | public CDMs (emergency-reportable only) | 8 h | 3/day |

Whether a feed is due is decided from `ingest_runs` in the database, not the local cache, so limits hold on any machine. Failed attempts count as contact, so a bad login can't retry in a loop. `--force` overrides; use it sparingly with Space-Track.

Raw downloads are also kept gzipped in `data/` locally (not in CI).

## Conjunction screening

`orbital screen` screens the watchlist against every object in orbit for the next 72 hours:

1. **Orbit-shell filter**: drop objects whose perigee-apogee band never comes within 50 km of a watched object's.
2. **Coarse search** on a 30 s grid: a KD-tree per step finds neighbours within `threshold x 1.5 + 16 km/s x step / 2` (no pair can cross between grid points unseen), then each pair's straight-line closest approach within the step.
3. **Refinement**: minimise the SGP4 distance around each candidate pass for TCA and miss distance, plus the radial / in-track / cross-track split.

Attached objects (station modules, docked vehicles: relative speed under 10 m/s, within 1 km) are skipped. Passes under 10 km are stored.

The watchlist is the `watchlist` table (ISS, Tiangong and Hubble to start) plus every object named in a Space-Track public CDM in the last 7 days, which keeps the comparison with Space-Track running on every run.

**Validation against Space-Track (Nov 2026):**

| Window | Space-Track events in scope | Found | TCA difference (median) | Miss-distance difference (median / 90th pct) |
| --- | --- | --- | --- | --- |
| 11-17 Nov, retrospective (6.5 days) | 78 | 78 | 0.08 s | 0.22 km / 1.09 km |
| 15-18 Nov, first scheduled run | 30 | 30 | 0.08 s | 0.17 km / 0.46 km |

Recall held across 0-4 days of element age. Ranked by our miss distance, Space-Track's events sit at median rank 22 of ~5,900 passes. Events with analyst objects (no public elements) are out of scope. The watchlist includes the CDM objects, so recall here measures screening accuracy, not whether we would have picked those objects.

**Collision probability** is a rough estimate: GP data has no covariance, so each object gets an assumed Gaussian (radial 0.1 km, cross-track 0.15 km, in-track 0.4 km + 0.3 km/day of element age), projected onto the encounter plane. It ranks events reasonably (52 of Space-Track's 78 events in our top 100) but does not reproduce Space-Track's values (Spearman 0.22, typically orders of magnitude lower), because a 0.2 km miss-distance difference dominates at these scales. The UI leads with miss distance and labels Pc an estimate. Slow encounters (under 100 m/s) get no Pc.

## Collision-risk forecaster

Predicts whether an event's final risk will reach 1e-6 from the CDMs released at least 2 days before closest approach: the ESA Kelvins Collision Avoidance Challenge task, scored with the challenge's own metric. Full write-up: [models/risk/MODEL_CARD.md](models/risk/MODEL_CARD.md).

| Model | Official test loss | vs naive baseline |
| --- | --- | --- |
| Latest known risk (naive baseline) | 0.694 | |
| Baseline + LightGBM classifier promotions | 0.692 | −0.4% |
| **Learned three-parameter rule (deployed)** | **0.634** | **−8.7%** |

Chosen by cross-validation on training events only (prior-corrected for the test set's published high-risk share), then scored on the test set once. LightGBM on 119 features did not beat the latest known risk. The API applies the rule to Space-Track's latest Pc for matched events and returns only a high/low flag (`forecast_high_risk`), never the Pc.

Data: download the [Kelvins dataset](https://zenodo.org/record/4463683) (CC-BY-4.0, 221 MB, md5 `d19dc8875229f2f6893253c38adddc87`) and extract `kelvins_competition_data/*` (including the CSV inside `train_data.zip`) into `data/kelvins/competition/`.

## Schedule

`.github/workflows/refresh.yml` runs every 30 minutes on GitHub Actions (GitHub drops scheduled runs under load, so it triggers often and each step checks whether it is due): `orbital refresh`, then `orbital screen` (at most every 20 h) and `orbital validate`. It needs repository secrets `DATABASE_URL`, `SPACETRACK_IDENTITY`, `SPACETRACK_PASSWORD` and `CELESTRAK_USER_AGENT`. Scheduled workflows only run from the default branch.

## Schema

- `objects`: one row per catalogued object, keyed by integer `norad_cat_id`.
- `gp_elements`: every element set ever ingested, primary key `(norad_cat_id, epoch)`, partitioned by month of epoch. Partitions are created on demand by ingest.
- `gp_latest`: view of the newest set per object.
- `ingest_runs`: one row per run with status and counts, for monitoring and rate limiting.
- `cdm_public`: Space-Track public conjunction messages. Private (RLS, no policies): the user agreement restricts redistribution.
- `watchlist`: objects screened against the catalog (plus recent CDM objects, added at run time).
- `screening_runs`: one row per screening run: window, parameters, timings, watchlist and validation summary.
- `conjunctions`: our close approaches per run (TCA, miss, relative speed, RTN components, Pc estimate).

Migrations: never apply one to the shared Supabase database from an unmerged branch. The scheduled refresh on `master` runs `alembic upgrade head` first and fails on a revision it doesn't have (this stopped refreshes for ~30 hours on 15-16 Nov 2026).

## Catalog numbers above 99999

Catalog numbers passed 99999 in July 2026. We read only OMM (never TLE) and store IDs as `integer`. python-sgp4 refuses satellite numbers above 339999, so `propagate.to_satrec` initialises SGP4 with a placeholder number; the number does not affect the orbit maths. `tests/test_propagate.py` covers 100000 and a nine-digit ID.
