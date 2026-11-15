"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import ObjectPanel from "@/components/ObjectPanel";
import { type Catalog, fetchCatalog, OBJECT_TYPES, type ObjectType } from "@/lib/catalog";

// Cesium touches window and WebGL at import time, so the globe is client-only.
const Globe = dynamic(() => import("@/components/Globe"), { ssr: false });

const MAX_RESULTS = 8;

export default function Dashboard() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("Loading catalog…");
  const [visible, setVisible] = useState<ReadonlySet<ObjectType>>(() => new Set(OBJECT_TYPES.map((t) => t.type)));
  const [selected, setSelected] = useState<number | null>(null);
  const [flyRequest, setFlyRequest] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    fetchCatalog(ctrl.signal)
      .then(setCatalog)
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    return () => ctrl.abort();
  }, []);

  const counts = useMemo(() => {
    const c: Record<ObjectType, number> = { PAY: 0, "R/B": 0, DEB: 0, UNK: 0 };
    catalog?.objects.forEach((o) => c[o.type]++);
    return c;
  }, [catalog]);

  // Search by name, international designator or catalog number.
  const results = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (!catalog || q.length < 2) return [];
    const isNumber = /^\d+$/.test(q);
    const out: number[] = [];
    for (let i = 0; i < catalog.objects.length && out.length < MAX_RESULTS; i++) {
      const { omm } = catalog.objects[i];
      const hit = isNumber
        ? String(omm.NORAD_CAT_ID).startsWith(q)
        : omm.OBJECT_NAME?.toUpperCase().includes(q) || omm.OBJECT_ID?.toUpperCase().startsWith(q);
      if (hit) out.push(i);
    }
    return out;
  }, [catalog, query]);

  const pick = (index: number) => {
    setSelected(index);
    setFlyRequest((n) => n + 1);
    setQuery("");
    // Make sure the picked object's type is visible.
    const type = catalog!.objects[index].type;
    if (!visible.has(type)) setVisible(new Set([...visible, type]));
  };

  const toggle = (type: ObjectType) => {
    const next = new Set(visible);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    setVisible(next);
  };

  return (
    <main className="relative h-full w-full">
      {catalog && (
        <Globe
          catalog={catalog}
          visibleTypes={visible}
          selected={selected}
          onSelect={setSelected}
          flyRequest={flyRequest}
          onStatus={setStatus}
        />
      )}

      <section className="pointer-events-auto absolute top-4 left-4 z-10 w-[min(22rem,calc(100vw-5rem))] rounded-lg border border-panel-border bg-panel p-4 text-sm shadow-xl backdrop-blur">
        <h1 className="text-base font-semibold tracking-tight">Orbital Collision-Risk Dashboard</h1>
        <p className="mt-0.5 text-xs text-muted">
          {catalog
            ? `${catalog.objects.length.toLocaleString()} objects · elements as of ${new Date(catalog.generatedAt).toLocaleString()}`
            : error
              ? "Could not load the catalog."
              : status}
        </p>
        {error && (
          <p className="mt-2 rounded bg-red-950/60 p-2 text-xs text-red-200">
            {error}. Is the API running on port 8000?
          </p>
        )}
        {catalog && status && <p className="mt-1 text-xs text-muted">{status}</p>}

        <div className="relative mt-3">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results.length) pick(results[0]);
              if (e.key === "Escape") setQuery("");
            }}
            placeholder="Search name, catalog number or designator"
            aria-label="Search objects"
            disabled={!catalog}
            className="w-full rounded-md border border-panel-border bg-black/40 px-3 py-2 text-base outline-none placeholder:text-muted focus:border-white/30 sm:text-sm"
          />
          {results.length > 0 && (
            <ul className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-md border border-panel-border bg-[#0d111c] shadow-xl">
              {results.map((i) => {
                const o = catalog!.objects[i];
                return (
                  <li key={i}>
                    <button
                      onClick={() => pick(i)}
                      className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left hover:bg-white/5"
                    >
                      <span className="truncate">{o.omm.OBJECT_NAME}</span>
                      <span className="shrink-0 font-mono text-xs text-muted">{o.omm.NORAD_CAT_ID}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <ul className="mt-3 grid gap-1">
          {OBJECT_TYPES.map(({ type, label, color }) => (
            <li key={type}>
              <button
                onClick={() => toggle(type)}
                aria-pressed={visible.has(type)}
                className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs transition-opacity ${
                  visible.has(type) ? "border-panel-border" : "border-transparent opacity-40"
                }`}
              >
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
                <span className="truncate">{label}</span>
                <span className="ml-auto font-mono text-muted">{counts[type].toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ul>

        <p className="mt-3 text-[11px] leading-snug text-muted">
          Educational project, not for operational decisions. Operators rely on official conjunction messages. Data:
          CelesTrak, Space-Track.
        </p>
      </section>

      {catalog && selected !== null && (
        <ObjectPanel key={selected} entry={catalog.objects[selected]} onClose={() => setSelected(null)} />
      )}
    </main>
  );
}
