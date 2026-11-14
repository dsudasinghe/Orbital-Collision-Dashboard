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
.venv\Scripts\alembic upgrade head          # create / update tables
.venv\Scripts\orbital ingest-gp             # fetch CelesTrak GROUP=active as OMM JSON and store it
.venv\Scripts\python -m pytest              # tests (no database needed)
```

`ingest-gp` reuses a download less than 2 hours old (CelesTrak's update interval and usage policy); `--force` overrides. Raw downloads are kept gzipped in `data/celestrak/<group>/`.

## Schema

- `objects`: one row per catalogued object, keyed by integer `norad_cat_id`.
- `gp_elements`: every element set ever ingested, primary key `(norad_cat_id, epoch)`, partitioned by month of epoch. Partitions are created on demand by ingest.
- `gp_latest`: view of the newest set per object.
- `ingest_runs`: one row per run with status and counts, for monitoring.

## Catalog numbers above 99999

Catalog numbers passed 99999 in July 2026. We read only OMM (never TLE) and store IDs as `integer`. python-sgp4 refuses satellite numbers above 339999, so `propagate.to_satrec` initialises SGP4 with a placeholder number; the number does not affect the orbit maths. `tests/test_propagate.py` covers 100000 and a nine-digit ID.
