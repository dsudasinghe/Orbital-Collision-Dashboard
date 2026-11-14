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
}

export async function fetchObject(id: number, signal?: AbortSignal): Promise<ObjectDetail> {
  const res = await fetch(`/api/objects/${id}`, { signal });
  if (!res.ok) throw new Error(`object request failed: HTTP ${res.status}`);
  return res.json();
}
