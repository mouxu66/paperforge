/**
 * Extension build script
 *
 * Usage:
 *   npm run build        # produces dist/paperforge-clipper-v<version>.zip
 *   npm run pack         # alias
 *
 * Cross-platform: uses adm-zip (Node-only dependency) so it works on Windows
 * without relying on PowerShell Compress-Archive or the zip CLI.
 */
const fs = require("node:fs");
const path = require("node:path");
const AdmZip = require("adm-zip");

const ROOT = path.resolve(__dirname, "..");
const DIST_DIR = path.join(ROOT, "dist");

/** Files / directories that should not be shipped in the extension package. */
const EXCLUDED = new Set([
  ".git",
  ".github",
  "node_modules",
  "dist",
  "scripts",
  "package.json",
  "package-lock.json",
  ".gitignore",
  ".prettierrc",
]);

function shouldInclude(name) {
  if (EXCLUDED.has(name)) return false;
  if (name.endsWith(".test.js")) return false;
  return true;
}

function main() {
  const manifestPath = path.join(ROOT, "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    console.error("[build] manifest.json not found at", ROOT);
    process.exit(1);
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  const version = manifest.version || "0.0.0";
  const zipName = `paperforge-clipper-v${version}.zip`;

  // Clean dist directory before each build to avoid stale artifacts
  // (e.g. old versions or deleted files lingering from previous runs).
  if (fs.existsSync(DIST_DIR)) {
    fs.rmSync(DIST_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(DIST_DIR, { recursive: true });

  const zip = new AdmZip();

  const entries = fs.readdirSync(ROOT, { withFileTypes: true });
  for (const entry of entries) {
    if (!shouldInclude(entry.name)) continue;

    const fullPath = path.join(ROOT, entry.name);
    if (entry.isDirectory()) {
      zip.addLocalFolder(fullPath, entry.name);
    } else {
      zip.addLocalFile(fullPath, "", entry.name);
    }
  }

  const zipPath = path.join(DIST_DIR, zipName);
  zip.writeZip(zipPath);

  const stats = fs.statSync(zipPath);
  console.log(`[build] ${zipName}`);
  console.log(`[build] size: ${(stats.size / 1024).toFixed(1)} KB`);
  console.log(`[build] output: ${zipPath}`);
}

main();
