# Code

- `backend/` — Python 3.12: OMM ingest worker, SGP4 propagation and conjunction screening, ML models (risk forecaster, anomaly detector), FastAPI.
- `frontend/` — Next.js + TypeScript + Tailwind + CesiumJS (Resium) dashboard.

Start with Phase 1 in `../docs/project-plan.md`: ingest CelesTrak GP data as OMM JSON and store every element set by `NORAD_CAT_ID` (integer).
