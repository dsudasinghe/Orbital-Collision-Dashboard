"use client";

import "@/lib/cesium-base";
import "cesium/Build/Cesium/Widgets/widgets.css";

import {
  ArcType,
  BlendOption,
  buildModuleUrl,
  Cartesian3,
  Color,
  ImageryLayer,
  JulianDate,
  Material,
  type PointPrimitive,
  PointPrimitiveCollection,
  type Polyline,
  PolylineCollection,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  TileMapServiceImageryProvider,
  Viewer,
} from "cesium";
import { useEffect, useRef, useState } from "react";

import { type Catalog, OBJECT_TYPES, type ObjectType } from "@/lib/catalog";
import { orbitTrack, periodMinutes } from "@/lib/orbit";
import type { WorkerRequest, WorkerResponse } from "@/workers/propagate.worker";

const POINT_SIZE = 2;
const SELECTED_SIZE = 9;
// Ask the worker for a fresh sample this often (wall clock) or when simulated
// time has drifted this far from the last sample (e.g. after scrubbing).
const RESAMPLE_WALL_MS = 500;
const RESAMPLE_DRIFT_MS = 60_000;

const TYPE_COLORS = Object.fromEntries(
  OBJECT_TYPES.map(({ type, color }) => [type, Color.fromCssColorString(color)]),
) as Record<ObjectType, Color>;

export interface GlobeProps {
  catalog: Catalog;
  visibleTypes: ReadonlySet<ObjectType>;
  selected: number | null; // index into catalog.objects
  onSelect: (index: number | null) => void;
  flyRequest: number; // increment to fly the camera to the selected object
  onStatus?: (status: string) => void;
}

export default function Globe({ catalog, visibleTypes, selected, onSelect, flyRequest, onStatus }: GlobeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const pointsRef = useRef<PointPrimitive[]>([]);
  const statesRef = useRef<{ timeMs: number; states: Float64Array } | null>(null);
  // Props read inside long-lived Cesium callbacks.
  const visibleRef = useRef(visibleTypes);
  const selectedRef = useRef(selected);
  const onSelectRef = useRef(onSelect);
  const onStatusRef = useRef(onStatus);
  useEffect(() => {
    visibleRef.current = visibleTypes;
    selectedRef.current = selected;
    onSelectRef.current = onSelect;
    onStatusRef.current = onStatus;
  });

  // The Cesium viewer itself, created once per mount.
  useEffect(() => {
    const v = new Viewer(containerRef.current!, {
      // Natural Earth ships with Cesium: no Cesium ion token or third-party tiles needed.
      baseLayer: ImageryLayer.fromProviderAsync(
        TileMapServiceImageryProvider.fromUrl(buildModuleUrl("Assets/Textures/NaturalEarthII")),
      ),
      baseLayerPicker: false,
      geocoder: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
    });
    v.scene.globe.enableLighting = true;
    setViewer(v);
    return () => {
      setViewer(null);
      v.destroy();
    };
  }, []);

  // Points, worker loop and picking. Rebuilt when the catalog changes.
  useEffect(() => {
    if (!viewer) return;
    const { scene } = viewer;

    const collection = scene.primitives.add(new PointPrimitiveCollection({ blendOption: BlendOption.OPAQUE }));
    const points: PointPrimitive[] = catalog.objects.map((obj, i) =>
      collection.add({ id: i, position: Cartesian3.ZERO, color: TYPE_COLORS[obj.type], pixelSize: POINT_SIZE, show: false }),
    );
    pointsRef.current = points;
    const types = catalog.objects.map((o) => o.type);

    const tracks = scene.primitives.add(new PolylineCollection());
    let track: Polyline | null = null;
    let trackFor: { index: number; startMs: number } | null = null;

    const worker = new Worker(new URL("../workers/propagate.worker.ts", import.meta.url), { type: "module" });
    let ready = false;
    let inFlight = false;
    let lastRequestWall = 0;
    let requestId = 0;
    statesRef.current = null;
    onStatusRef.current?.("Preparing orbits…");

    worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
      const msg = event.data;
      if (msg.kind === "ready") {
        ready = true;
        onStatusRef.current?.("");
      } else if (msg.requestId === requestId) {
        statesRef.current = { timeMs: msg.timeMs, states: msg.states };
        inFlight = false;
      }
    };
    worker.postMessage({ kind: "init", omms: catalog.objects.map((o) => o.omm) } satisfies WorkerRequest);

    const scratch = new Cartesian3();
    const removeTick = scene.preRender.addEventListener((_scene, time: JulianDate) => {
      const nowMs = JulianDate.toDate(time).getTime();
      const wall = performance.now();
      const sample = statesRef.current;

      if (ready && !inFlight && (!sample || wall - lastRequestWall > RESAMPLE_WALL_MS || Math.abs(nowMs - sample.timeMs) > RESAMPLE_DRIFT_MS)) {
        inFlight = true;
        lastRequestWall = wall;
        requestId++;
        // Sample slightly ahead at speed-up so points don't trail the clock.
        const ahead = (RESAMPLE_WALL_MS / 2) * viewer.clock.multiplier;
        worker.postMessage({ kind: "sample", requestId, timeMs: nowMs + ahead } satisfies WorkerRequest);
      }
      if (!sample) return;

      // Move each point along its velocity from the last sample.
      const dt = (nowMs - sample.timeMs) / 1000;
      const s = sample.states;
      const visible = visibleRef.current;
      for (let i = 0; i < points.length; i++) {
        const o = i * 6;
        const x = s[o];
        const point = points[i];
        if (Number.isNaN(x) || !visible.has(types[i])) {
          point.show = false;
          continue;
        }
        scratch.x = x + s[o + 3] * dt;
        scratch.y = s[o + 1] + s[o + 4] * dt;
        scratch.z = s[o + 2] + s[o + 5] * dt;
        point.position = scratch;
        point.show = true;
      }

      // Orbit path for the selected object, redrawn as time moves on.
      const sel = selectedRef.current;
      if (sel === null) {
        if (track) {
          tracks.remove(track);
          track = null;
          trackFor = null;
        }
      } else {
        const omm = catalog.objects[sel].omm;
        const stale = !trackFor || trackFor.index !== sel || Math.abs(nowMs - trackFor.startMs) > (periodMinutes(omm) * 60_000) / 40;
        if (stale) {
          if (track) tracks.remove(track);
          track = tracks.add({
            positions: Cartesian3.unpackArray(orbitTrack(omm, nowMs)),
            width: 1.5,
            arcType: ArcType.NONE,
            material: Material.fromType("Color", { color: Color.WHITE.withAlpha(0.6) }),
          });
          trackFor = { index: sel, startMs: nowMs };
        }
      }
    });

    const handler = new ScreenSpaceEventHandler(scene.canvas);
    handler.setInputAction((click: ScreenSpaceEventHandler.PositionedEvent) => {
      const picked = scene.pick(click.position);
      onSelectRef.current(picked && typeof picked.id === "number" ? picked.id : null);
    }, ScreenSpaceEventType.LEFT_CLICK);

    return () => {
      removeTick();
      handler.destroy();
      worker.terminate();
      pointsRef.current = [];
      if (!viewer.isDestroyed()) {
        scene.primitives.remove(collection);
        scene.primitives.remove(tracks);
      }
    };
  }, [viewer, catalog]);

  // Highlight the selected point.
  useEffect(() => {
    const points = pointsRef.current;
    if (selected === null || !points[selected]) return;
    const point = points[selected];
    point.pixelSize = SELECTED_SIZE;
    point.color = Color.WHITE;
    point.outlineColor = TYPE_COLORS[catalog.objects[selected].type];
    point.outlineWidth = 2;
    return () => {
      point.pixelSize = POINT_SIZE;
      point.color = TYPE_COLORS[catalog.objects[selected].type];
      point.outlineWidth = 0;
    };
  }, [selected, catalog, viewer]);

  // Fly to the selected object when asked (search results), not on every click.
  useEffect(() => {
    const sample = statesRef.current;
    const sel = selectedRef.current;
    if (!flyRequest || !viewer || sel === null || !sample) return;
    const o = sel * 6;
    const p = new Cartesian3(sample.states[o], sample.states[o + 1], sample.states[o + 2]);
    if (Number.isNaN(p.x)) return;
    const altitude = Cartesian3.magnitude(p);
    const destination = Cartesian3.multiplyByScalar(Cartesian3.normalize(p, new Cartesian3()), altitude + Math.max(7e6, altitude * 0.6), new Cartesian3());
    viewer.camera.flyTo({ destination, duration: 1.5 });
  }, [flyRequest, viewer]);

  return <div ref={containerRef} className="absolute inset-0" />;
}
