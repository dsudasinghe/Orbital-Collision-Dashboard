"use client";

import type { ConjunctionEvent, ConjunctionsResponse } from "@/lib/catalog";

function formatMiss(km: number): string {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(2)} km`;
}

function formatTca(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const MAX_LISTED = 12;

export function ConjunctionList({
  data,
  active,
  onFocus,
}: {
  data: ConjunctionsResponse;
  active: ConjunctionEvent | null;
  onFocus: (event: ConjunctionEvent) => void;
}) {
  const { run, events } = data;
  if (!run) return <p className="text-xs text-muted">No screening run yet.</p>;
  const v = run.validation;
  return (
    <>
      <p className="text-[11px] leading-snug text-muted">
        Our SGP4 screening of {run.watch_count} watched objects against the catalog, next 72 h.
        {v?.in_scope ? ` Found ${v.matched} of ${v.in_scope} Space-Track public events in this window.` : ""}
      </p>
      <ul className="mt-2 max-h-56 space-y-0.5 overflow-y-auto pr-1">
        {events.slice(0, MAX_LISTED).map((e) => {
          const isActive = active && active.sat1_id === e.sat1_id && active.sat2_id === e.sat2_id && active.tca === e.tca;
          return (
            <li key={`${e.sat1_id}-${e.sat2_id}-${e.tca}`}>
              <button
                onClick={() => onFocus(e)}
                className={`w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-white/5 ${isActive ? "bg-white/10" : ""}`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="truncate">
                    {e.sat1_name} × {e.sat2_name}
                  </span>
                  <span className="shrink-0 font-mono">{formatMiss(e.miss_km)}</span>
                </span>
                <span className="flex justify-between gap-2 text-[11px] text-muted">
                  <span>{formatTca(e.tca)}</span>
                  <span className="flex gap-2">
                    {e.forecast_high_risk && (
                      <span className="text-red-300" title="Risk forecaster (experimental): final risk likely ≥ 1e-6">
                        high risk
                      </span>
                    )}
                    {e.in_spacetrack && <span title="Space-Track also published this event">Space-Track ✓</span>}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-mono">{value ?? "—"}</dd>
    </div>
  );
}

export function EventPanel({ event, onClose }: { event: ConjunctionEvent; onClose: () => void }) {
  return (
    <aside className="absolute right-4 bottom-10 left-4 z-10 max-h-[45vh] overflow-y-auto rounded-lg border border-panel-border bg-panel p-4 text-sm shadow-xl backdrop-blur sm:top-4 sm:bottom-auto sm:left-auto sm:max-h-[calc(100vh-2rem)] sm:w-80">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">Close approach</p>
          <h2 className="text-base leading-snug font-semibold">
            {event.sat1_name} <span className="text-muted">×</span> {event.sat2_name}
          </h2>
          <p className="mt-0.5 font-mono text-xs text-muted">
            #{event.sat1_id} × #{event.sat2_id}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close" className="-m-1 rounded p-1 text-muted hover:bg-white/10 hover:text-foreground">
          ✕
        </button>
      </div>

      <dl className="mt-3 divide-y divide-white/5">
        <Row label="Time of closest approach" value={new Date(event.tca).toLocaleString()} />
        <Row label="Miss distance" value={formatMiss(event.miss_km)} />
        <Row label="Relative speed" value={`${event.rel_speed_kms.toFixed(1)} km/s`} />
        <Row label="Radial / in-track / cross" value={`${event.radial_km.toFixed(2)} / ${event.in_track_km.toFixed(2)} / ${event.cross_track_km.toFixed(2)} km`} />
        <Row label="Collision probability*" value={event.pc_estimate != null ? event.pc_estimate.toExponential(1) : "n/a"} />
        <Row label="In Space-Track public CDMs" value={event.in_spacetrack ? "Yes" : "No"} />
        <Row
          label="Risk forecast†"
          value={
            event.forecast_high_risk == null
              ? "n/a"
              : `${event.forecast_high_risk ? "High" : "Low"} (${event.forecast_lead_days} d ahead)`
          }
        />
      </dl>

      <p className="mt-3 text-[11px] leading-snug text-muted">
        The globe has jumped to 90 s before closest approach. *Rough estimate from public GP data with an assumed
        covariance: miss distances are typically within ~0.2 km of Space-Track&apos;s, but probabilities can differ by
        orders of magnitude. †Whether the final risk will reach 1e-6, from Space-Track&apos;s latest Pc and a rule
        learned on ESA&apos;s Kelvins dataset (8.7% better than the naive baseline on held-out events). Experimental.
        Not for operational use.
      </p>
    </aside>
  );
}
