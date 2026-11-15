# Frontend

Next.js 16 + TypeScript + Tailwind 4 + CesiumJS 1.146 dashboard. Every catalogued object in orbit on a 3D globe, propagated in the browser with satellite.js.

## Run

Start the API first (see `../backend/README.md`), then:

```powershell
npm install
npm run dev        # http://localhost:3000
```

`/api/*` is proxied to the FastAPI backend (`API_URL`, default `http://127.0.0.1:8000`), so the browser sees one origin.

`predev` / `prebuild` copy Cesium's workers, widgets and textures from `node_modules/cesium` into `public/cesium` (git-ignored); Cesium loads them at runtime from `CESIUM_BASE_URL`.

## How the globe works

- `GET /api/catalog` returns the latest OMM element set per object (about 33k, 1.9 MB gzipped).
- A Web Worker (`src/workers/propagate.worker.ts`) builds a satellite.js `SatRec` per object with `json2satrec`, then about twice a second propagates the whole catalog (30-40 ms) and returns Earth-fixed position and velocity for each.
- The render loop moves each point along its velocity between samples, so motion is smooth at 60 fps while propagation stays off the main thread.
- Points are one `PointPrimitiveCollection` coloured by object type; clicking picks by index. The selected object gets a one-orbit path, recomputed as time advances.
- Imagery is Natural Earth II, bundled with Cesium: no Cesium ion token needed.

satellite.js 7.1.0 and the backend's python-sgp4 agree to the millimetre on the test fixtures, including catalog numbers 100000 and 123456789 (satellite.js keeps the number as a string, so it has no 339999 limit).
