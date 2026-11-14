// Cesium loads its web workers, widget CSS and textures at runtime from
// CESIUM_BASE_URL, so they must be served as static files. Copy them from the
// installed package into public/cesium (git-ignored) before dev and build.
import { cpSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const pkgDir = dirname(require.resolve("cesium/package.json"));
const version = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8")).version;
const src = join(pkgDir, "Build", "Cesium");
const dest = join(root, "public", "cesium");
const stamp = join(dest, ".version");

if (existsSync(stamp) && readFileSync(stamp, "utf8") === version) {
  process.exit(0);
}
for (const dir of ["Workers", "ThirdParty", "Assets", "Widgets"]) {
  cpSync(join(src, dir), join(dest, dir), { recursive: true });
}
writeFileSync(stamp, version);
console.log(`copied Cesium ${version} static files to public/cesium`);
