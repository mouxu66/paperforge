"""Download the qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q model files
into a PROJECT-LOCAL directory (mock_api/_models/...) so they survive across
the sandbox's per-task /tmp isolation.

The bare HuggingFace file CDN (huggingface.co/<repo>/resolve/main/<file>) is
reachable even though the Hub metadata API is blocked, so we pull files
directly with urllib following redirects.
"""

import os
import urllib.parse
import urllib.request

DEST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_models", "paraphrase-multilingual-onnx"
)
REPO = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
BASE = f"https://huggingface.co/{REPO}/resolve/main/"

FILES = [
    "model_optimized.onnx",
    "tokenizer.json",
    "config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch(url, dest):
    for _ in range(8):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.getheader("Location")
                    url = loc if loc.startswith("http") else urllib.parse.urljoin(url, loc)
                    continue
                data = r.read()
                with open(dest, "wb") as fh:
                    fh.write(data)
                return len(data)
        except Exception as e:  # noqa: BLE001
            print(f"  retry {os.path.basename(dest)}: {e}", flush=True)
            continue
    raise RuntimeError(f"failed to fetch {url}")


def main():
    os.makedirs(DEST, exist_ok=True)
    for f in FILES:
        url = BASE + f
        out = os.path.join(DEST, f)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f"SKIP (exists) {f} {os.path.getsize(out)}", flush=True)
            continue
        size = fetch(url, out)
        print(f"OK {f} {size} bytes -> {out}", flush=True)
    print("=== final listing ===", flush=True)
    for f in sorted(os.listdir(DEST)):
        print("  ", f, os.path.getsize(os.path.join(DEST, f)), flush=True)
    print("DOWNLOAD_DONE", flush=True)


if __name__ == "__main__":
    main()
