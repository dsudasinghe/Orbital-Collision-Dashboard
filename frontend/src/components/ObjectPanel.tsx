"use client";

import { useEffect, useState } from "react";

import { type CatalogObject, fetchObject, OBJECT_TYPES, type ObjectDetail } from "@/lib/catalog";
import { periodMinutes } from "@/lib/orbit";

const OWNER_NAMES: Record<string, string> = {
  US: "United States", CIS: "Russia / CIS", PRC: "China", ISS: "ISS partners", JPN: "Japan",
  ESA: "ESA", IND: "India", FR: "France", UK: "United Kingdom", POR: "Portugal",
};

function age(iso: string): string {
  const hours = (Date.now() - Date.parse(iso)) / 3_600_000;
  return hours < 48 ? `${hours.toFixed(1)} h ago` : `${(hours / 24).toFixed(1)} days ago`;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-mono">{value ?? "—"}</dd>
    </div>
  );
}

export default function ObjectPanel({ entry, onClose }: { entry: CatalogObject; onClose: () => void }) {
  const { omm } = entry;
  const [detail, setDetail] = useState<ObjectDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchObject(omm.NORAD_CAT_ID, ctrl.signal)
      .then(setDetail)
      .catch(() => !ctrl.signal.aborted && setError(true));
    return () => ctrl.abort();
  }, [omm.NORAD_CAT_ID]);

  const typeInfo = OBJECT_TYPES.find((t) => t.type === entry.type)!;
  const o = detail?.object;
  const conj = detail?.conjunctions;

  return (
    <aside className="absolute right-4 bottom-10 left-4 z-10 max-h-[45vh] overflow-y-auto rounded-lg border border-panel-border bg-panel p-4 text-sm shadow-xl backdrop-blur sm:top-4 sm:bottom-auto sm:left-auto sm:max-h-[calc(100vh-2rem)] sm:w-80">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{omm.OBJECT_NAME}</h2>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
            <span className="size-2 rounded-full" style={{ background: typeInfo.color }} />
            {typeInfo.label.replace(/s$/, "")} · #{omm.NORAD_CAT_ID} · {omm.OBJECT_ID}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close" className="-m-1 rounded p-1 text-muted hover:bg-white/10 hover:text-foreground">
          ✕
        </button>
      </div>

      <dl className="mt-3 divide-y divide-white/5">
        <Row label="Owner" value={entry.owner ? (OWNER_NAMES[entry.owner] ?? entry.owner) : null} />
        <Row label="Launched" value={o?.launch_date} />
        <Row label="Period" value={`${periodMinutes(omm).toFixed(1)} min`} />
        <Row label="Inclination" value={`${omm.INCLINATION.toFixed(2)}°`} />
        <Row label="Perigee × apogee" value={o?.perigee_km != null ? `${o.perigee_km} × ${o.apogee_km} km` : null} />
        <Row label="Eccentricity" value={omm.ECCENTRICITY.toFixed(5)} />
        <Row label="Element set" value={age(omm.EPOCH + "Z")} />
        <Row label="Sets stored" value={detail?.elements.length === 50 ? "50+" : detail?.elements.length} />
      </dl>

      <h3 className="mt-4 text-xs font-semibold tracking-wide text-muted uppercase">Public conjunction messages</h3>
      {error && <p className="mt-1 text-xs text-red-300">Couldn&apos;t load details.</p>}
      {conj && conj.events === 0 && <p className="mt-1 text-xs text-muted">None in the data we hold.</p>}
      {conj && conj.events > 0 && (
        <dl className="mt-1 divide-y divide-white/5">
          <Row label="Events" value={conj.events} />
          <Row label="Closest approach" value={conj.closest_m != null ? `${Math.round(conj.closest_m)} m` : null} />
          <Row label="Highest Pc" value={conj.max_pc != null ? conj.max_pc.toExponential(1) : null} />
          <Row label="Next TCA" value={conj.next_tca ? new Date(conj.next_tca).toLocaleString() : null} />
        </dl>
      )}
    </aside>
  );
}
