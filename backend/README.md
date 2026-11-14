# Backend

Python service for ingest, propagation, conjunction screening and ML. Data lives in Supabase Postgres.

## Setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env   # then fill in DATABASE_URL
```

`DATABASE_URL` is the **Session pooler** string from Supabase (Project → Connect). It works over IPv4; the direct connection is IPv6-only on free projects.

## Commands

```powershell
.venv\Scripts\orbital db-check              # test the connection
.venv\Scriptslembic upgrade head          # create / update tables
.venv\Scripts\orbital refresh               # every feed that is due (what the scheduler runs)
.venv\Scripts\orbital ingest-gp             # CelesTrak GROUP=active (operational payloads), OMM JSON
.venv\Scripts\orbital ingest-satcat         # CelesTrak SATCAT: type, owner, launch/decay dates
.venv\Scripts\orbital ingest-catalog        # Space-Track full GP catalog, debris included
.venv\Scripts\orbital ingest-cdm --days 3   # Space-Track public conjunction messages
.venv\Scripts\python -m pytest              # tests (no database needed)
```

## Feeds and rate limits

| Source | Feed | Minimum interval | Provider limit |
| --- | --- | --- | --- |
| `celestrak:active` | GP, active payloads | 2 h | don't refetch within the ~2 h update cycle |
| `celestrak:satcat` | SATCAT CSV | 24 h | regenerated daily |
| `spacetrack:gp` | GP, full catalog | 8 h | 1/hour |
| `spacetrack:cdm_public` | public CDMs (emergency-reportable only) | 8 h | 3/day |

Whether a feed is due is decided from `ingest_runs` in the database, not the local cache, so limits hold on any machine. Failed attempts count as contact, so a bad login can't retry in a loop. `--force` overrides; use it sparingly with Space-Track.

Raw downloads are also kept gzipped in `data/` locally (not in CI).

## Schedule

`.github/workflows/refresh.yml` runs `orbital refresh` every 2 hours on GitHub Actions. It needs repository secrets `DATABASE_URL`, `SPACETRACK_IDENTITY`, `SPACETRACK_PASSWORD` and `CELESTRAK_USER_AGENT`. Scheduled workflows only run from the default branch.

## Schema

- `objects`: one row per catalogued object, keyed by integer `norad_cat_id`.
- `gp_elements`: every element set ever ingested, primary key `(norad_cat_id, epoch)`, partitioned by month of epoch. Partitions are created on demand by ingest.
- `gp_latest`: view of the newest set per object.
- `ingest_runs`: one row per run with status and counts, for monitoring and rate limiting.
- `cdm_public`: Space-Track public conjunction messages. Private (RLS, no policies): the user agreement restricts redistribution.

## Catalog numbers above 99999

Catalog numbers passed 99999 in July 2026. We read only OMM (never TLE) and store IDs as `integer`. python-sgp4 refuses satellite numbers above 339999, so `propagate.to_satrec` initialises SGP4 with a placeholder number; the number does not affect the orbit maths. `tests/test_propagate.py` covers 100000 and a nine-digit ID.
