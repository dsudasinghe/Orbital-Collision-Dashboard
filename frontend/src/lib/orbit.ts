import { eciToEcf, gstime, json2satrec, propagate } from "satellite.js";
import type { Omm } from "@/lib/catalog";

export function periodMinutes(omm: Omm): number {
  return 1440 / omm.MEAN_MOTION;
}

/**
 * Earth-fixed positions (metres, flat [x, y, z, ...]) over one orbit starting at
 * `startMs`, for drawing the selected object's path relative to the ground.
 */
export function orbitTrack(omm: Omm, startMs: number, steps = 240): number[] {
  const sat = json2satrec(omm as Parameters<typeof json2satrec>[0]);
  const stepMs = (periodMinutes(omm) * 60_000) / steps;
  const out: number[] = [];
  for (let k = 0; k <= steps; k++) {
    const date = new Date(startMs + k * stepMs);
    const pv = propagate(sat, date);
    if (!pv?.position) continue;
    const r = eciToEcf(pv.position, gstime(date));
    out.push(r.x * 1000, r.y * 1000, r.z * 1000);
  }
  return out;
}

/** Earth-fixed position (metres) at `ms`, or null if propagation fails. */
export function positionEcf(omm: Omm, ms: number): [number, number, number] | null {
  const sat = json2satrec(omm as Parameters<typeof json2satrec>[0]);
  const date = new Date(ms);
  const pv = propagate(sat, date);
  if (!pv?.position) return null;
  const r = eciToEcf(pv.position, gstime(date));
  return [r.x * 1000, r.y * 1000, r.z * 1000];
}
