// Propagates the whole catalog off the main thread.
//
// The main thread asks for a sample at a simulation time; we reply with
// Earth-fixed (ECEF) position and velocity for every object, in metres and
// metres/second, packed as [x, y, z, vx, vy, vz] per object. The globe moves
// points along the velocity between samples, so motion stays smooth while a
// full propagation (~30-40 ms for 33k objects) runs about once a second.

import { eciToEcf, gstime, json2satrec, propagate, type SatRec } from "satellite.js";
import type { Omm } from "@/lib/catalog";

export type WorkerRequest =
  | { kind: "init"; omms: Omm[] }
  | { kind: "sample"; requestId: number; timeMs: number };

export type WorkerResponse =
  | { kind: "ready"; initFailures: number }
  | { kind: "sample"; requestId: number; timeMs: number; states: Float64Array };

const EARTH_ROTATION_RAD_S = 7.2921150e-5;

let sats: (SatRec | null)[] = [];

function sample(timeMs: number): Float64Array {
  const out = new Float64Array(sats.length * 6).fill(NaN);
  const date = new Date(timeMs);
  const gmst = gstime(date);
  for (let i = 0; i < sats.length; i++) {
    const sat = sats[i];
    if (!sat) continue;
    const pv = propagate(sat, date);
    if (!pv || !pv.position || !pv.velocity) continue; // decayed or diverged: leave NaN
    const r = eciToEcf(pv.position, gmst);
    const v = eciToEcf(pv.velocity, gmst); // rotation only
    // Earth-fixed velocity also drops the frame's own rotation: v - w x r.
    const o = i * 6;
    out[o] = r.x * 1000;
    out[o + 1] = r.y * 1000;
    out[o + 2] = r.z * 1000;
    out[o + 3] = (v.x + EARTH_ROTATION_RAD_S * r.y) * 1000;
    out[o + 4] = (v.y - EARTH_ROTATION_RAD_S * r.x) * 1000;
    out[o + 5] = v.z * 1000;
  }
  return out;
}

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data;
  if (msg.kind === "init") {
    let failures = 0;
    sats = msg.omms.map((omm) => {
      try {
        // satellite.js keeps the catalog number as a string, so 6-9 digit IDs are fine.
        return json2satrec(omm as Parameters<typeof json2satrec>[0]);
      } catch {
        failures++;
        return null;
      }
    });
    self.postMessage({ kind: "ready", initFailures: failures } satisfies WorkerResponse);
  } else {
    const states = sample(msg.timeMs);
    self.postMessage(
      { kind: "sample", requestId: msg.requestId, timeMs: msg.timeMs, states } satisfies WorkerResponse,
      { transfer: [states.buffer] },
    );
  }
};
