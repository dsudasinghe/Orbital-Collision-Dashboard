# Code

- `backend/`: Python 3.13. Ingest (CelesTrak, Space-Track), SGP4 propagation, conjunction screening, ML models, FastAPI. See `backend/README.md`.
- `frontend/`: Next.js + TypeScript + Tailwind + CesiumJS dashboard. See `frontend/README.md`.

Project plan: `../docs/project-plan.md`.

## Run locally

```powershell
# terminal 1: API on :8000
cd backend
.venv\Scripts\python -m uvicorn orbital.api:app --reload --port 8000

# terminal 2: dashboard on :3000
cd frontend
npm run dev
```

Data refreshes every 2 hours via GitHub Actions (`.github/workflows/refresh.yml`).
