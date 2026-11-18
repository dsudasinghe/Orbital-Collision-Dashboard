// Catalog as served by GET /api/catalog: columnar rows with OMM keyword names,
// so each row can be passed straight to satellite.js json2satrec.

export type ObjectType = "PAY" | "R/B" | "DEB" | "UNK";

export const OBJECT_TYPES: { type: ObjectType; label: string; color: string }[] = [
  { type: "PAY", label: "Payloads", color: "#4fd1c5" },
  { type: "R/B", label: "Rocket bodies", color: "#f6ad55" },
  { type: "DEB", label: "Debris", color: "#fc8181" },
  { type: "UNK", label: "Unknown", color: "#b794f4" },
];

export interface Omm {
  NORAD_CAT_ID: number;
  OBJECT_NAME: string;
  OBJECT_ID: string;
  EPOCH: string;
  MEAN_MOTION: number;
  ECCENTRICITY: number;
  INCLINATION: number;
  RA_OF_ASC_NODE: number;
  ARG_OF_PERICENTER: number;
  MEAN_ANOMALY: number;
  BSTAR: number;
  MEAN_MOTION_DOT: number;
  MEAN_MOTION_DDOT: number;
  EPHEMERIS_TYPE: number;
  CLASSIFICATION_TYPE: string;
  ELEMENT_SET_NO: number;
  REV_AT_EPOCH: number;
}

export interface CatalogObject {
  omm: Omm;
  type: ObjectType;
  owner: string | null;
}

export interface Catalog {
  generatedAt: string;
  objects: CatalogObject[];
}

interface CatalogResponse {
  generated_at: string;
  count: number;
  fields: string[];
  rows: unknown[][];
}

export async function fetchCatalog(signal?: AbortSignal): Promise<Catalog> {
  const res = await fetch("/api/catalog", { signal });
  if (!res.ok) throw new Error(`catalog request failed: HTTP ${res.status}`);
  const data: CatalogResponse = await res.json();
  const objects = data.rows.map((row) => {
    const rec = Object.fromEntries(data.fields.map((f, i) => [f, row[i]])) as Record<string, unknown>;
    const { OBJECT_TYPE, OWNER, ...omm } = rec;
    const type = (["PAY", "R/B", "DEB"].includes(OBJECT_TYPE as string) ? OBJECT_TYPE : "UNK") as ObjectType;
    return { omm: omm as unknown as Omm, type, owner: (OWNER as string | null) ?? null };
  });
  return { generatedAt: data.generated_at, objects };
}

export interface ObjectDetail {
  object: {
    norad_cat_id: number;
    object_name: string | null;
    object_id: string | null;
    object_type: string | null;
    ops_status_code: string | null;
    owner: string | null;
    launch_date: string | null;
    launch_site: string | null;
    period_min: number | null;
    inclination_deg: number | null;
    apogee_km: number | null;
    perigee_km: number | null;
    rcs_m2: number | null;
  };
  elements: { epoch: string; mean_motion: number; eccentricity: number; inclination: number; bstar: number }[];
  conjunctions: { events: number; closest_m: number | null; max_pc: number | null; next_tca: string | null };
  // Detected from element history; "anomaly" is an unexplained change on an object that can't manoeuvre.
  manoeuvres: { epoch: string; kind: "orbit raise" | "orbit lower" | "plane change" | "anomaly"; da_km: number; d_inc_deg: number }[];
  anomaly: { score: number; scored_at: string } | null;
}

export async function fetchObject(id: number, signal?: AbortSignal): Promise<ObjectDetail> {
  const res = await fetch(`/api/objects/${id}`, { signal });
  if (!res.ok) throw new Error(`object request failed: HTTP ${res.status}`);
  return res.json();
}

export interface ConjunctionEvent {
  sat1_id: number;
  sat1_name: string | null;
  sat1_type: string | null;
  sat2_id: number;
  sat2_name: string | null;
  sat2_type: string | null;
  tca: string;
  miss_km: number;
  rel_speed_kms: number;
  radial_km: number;
  in_track_km: number;
  cross_track_km: number;
  pc_estimate: number | null;
  in_spacetrack: boolean;
  // A manoeuvre or anomaly newer than the elements this screening used: the miss distance may be stale.
  sat1_event: string | null;
  sat2_event: string | null;
  stale: boolean;
  // Risk forecaster on Space-Track's latest Pc (experimental): will the final risk be >= 1e-6?
  forecast_high_risk: boolean | null;
  forecast_lead_days: number | null;
}

export interface ConjunctionsResponse {
  run: {
    id: number;
    finished_at: string;
    window_start: string;
    window_end: string;
    watch_count: number;
    validation: { in_scope?: number; matched?: number; miss_diff_km_median?: number } | null;
  } | null;
  events: ConjunctionEvent[];
}

export async function fetchConjunctions(signal?: AbortSignal): Promise<ConjunctionsResponse> {
  const res = await fetch("/api/conjunctions?limit=50&max_km=5", { signal });
  if (!res.ok) throw new Error(`conjunctions request failed: HTTP ${res.status}`);
  return res.json();
}
