// Must be imported before "cesium": Cesium resolves its workers and assets
// from this URL (files copied by scripts/copy-cesium.mjs).
declare global {
  interface Window {
    CESIUM_BASE_URL?: string;
  }
}

if (typeof window !== "undefined") {
  window.CESIUM_BASE_URL = "/cesium/";
}

export {};
