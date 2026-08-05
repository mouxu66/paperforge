import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// web/scripts -> repo root
const repoRoot = path.resolve(__dirname, "..", "..");
const sourceDir = path.join(repoRoot, "extension", "dist");
const targetDir = path.join(repoRoot, "web", "public", "extension");
const targetPath = path.join(targetDir, "paperforge-clipper.zip");

const ZIP_PATTERN = /^paperforge-clipper-v[\d.]+\.zip$/;

async function main() {
  let entries;
  try {
    entries = await fs.readdir(sourceDir, { withFileTypes: true });
  } catch (err) {
    throw new Error(
      `Could not read extension dist directory: ${sourceDir}. ` +
        `Make sure the extension has been built (cd extension && npm run build).\n${err.message}`
    );
  }

  const zips = entries
    .filter((entry) => entry.isFile() && ZIP_PATTERN.test(entry.name))
    .map((entry) => path.join(sourceDir, entry.name));

  if (zips.length === 0) {
    throw new Error(
      `No paperforge-clipper-v*.zip found in ${sourceDir}. ` +
        `Please build the extension first (cd extension && npm run build).`
    );
  }

  // Pick the most recently modified matching zip
  const zipsWithStats = await Promise.all(
    zips.map(async (filePath) => ({ filePath, stat: await fs.stat(filePath) }))
  );
  zipsWithStats.sort((a, b) => b.stat.mtime - a.stat.mtime);
  const latest = zipsWithStats[0].filePath;

  await fs.mkdir(targetDir, { recursive: true });
  await fs.copyFile(latest, targetPath);

  console.log(
    `[sync-extension-zip] ${path.relative(repoRoot, latest)} → ${path.relative(
      repoRoot,
      targetPath
    )}`
  );
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
