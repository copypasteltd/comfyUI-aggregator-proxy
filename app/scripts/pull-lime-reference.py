"""Pull the user-selected 04 Lime source, or check its committed file hashes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://html.cafe/x5b381ea1"
DEST = ROOT / "apps/admin_web/src/theme/reference"
LOCK = ROOT / "config/lime-reference.lock.json"
MAX_BYTES = 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pull", action="store_true", help="Explicitly download the fixed reference URL")
    args = parser.parse_args()
    if args.pull:
        data = bytearray()
        with httpx.Client(timeout=30, follow_redirects=False) as client, client.stream("GET", SOURCE) as response:
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                raise ValueError("Reference must be HTML")
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_BYTES:
                    raise ValueError("Reference exceeds the fixed download budget")
                data.extend(chunk)
        source = bytes(data)
        text = source.decode("utf-8")
        start = text.rfind("/*", 0, text.index('[data-c="lime"]'))
        end = text.index("/* @@CONCEPT_CSS@@ */", start)
        css = text[start:end].encode("utf-8")
        if "04 青柠 Lime" not in css.decode("utf-8") or ".li-card" not in css.decode("utf-8"):
            raise ValueError("04 Lime source boundaries changed; inspect the source before adapting")
        fonts = []
        runtime_fonts = []
        font_files = []
        font_records = []
        for family in ("Manrope", "Anton"):
            match = re.search(r'@font-face\{font-family:"' + family + r'";[^}]+\}', text)
            if match is None:
                raise ValueError("Reference font declaration missing")
            declaration = match.group().encode("utf-8")
            encoded = re.search(rb"data:font/woff;base64,([^)]*)", declaration)
            if encoded is None:
                raise ValueError("Reference font must be self-contained WOFF")
            font = base64.b64decode(encoded.group(1), validate=True)
            if not font.startswith(b"wOFF"):
                raise ValueError("Reference font format mismatch")
            font_path = ROOT / f"apps/admin_web/src/assets/fonts/{family}-Reference.woff"
            font_path.parent.mkdir(parents=True, exist_ok=True)
            font_path.write_bytes(font)
            font_files.append(font_path)
            runtime_fonts.append(declaration.replace(b"data:font/woff;base64," + encoded.group(1), f"../assets/fonts/{family}-Reference.woff".encode("ascii")))
            fonts.append(declaration)
            font_records.append({"family": family, "format": "woff", "bytes": len(font), "sha256": digest(font), "declaration_sha256": digest(declaration)})
        font_css = b"\n".join(fonts) + b"\n"
        private = ROOT / ".local/design-reference"
        private.mkdir(parents=True, exist_ok=True)
        (private / "html-cafe-x5b381ea1.html").write_bytes(source)
        component_at = text.index('const Top = (cta) => `<header class="li-top')
        component_start = text.rfind("  (function () {", 0, component_at)
        components = text[component_start:text.index("/* @@CONCEPT_JS@@ */", component_at)].encode("utf-8")
        (private / "lime.components.js").write_bytes(components)
        DEST.mkdir(parents=True, exist_ok=True)
        files = {"lime.css": css, "fonts.css": font_css}
        for name, content in files.items():
            (DEST / name).write_bytes(content)
        runtime_css = DEST.parent / "fonts.css"
        runtime_css.write_bytes(b"\n".join(runtime_fonts) + b"\n")
        licenses = {f"apps/admin_web/src/assets/fonts/{family}-OFL.txt": digest((ROOT / f"apps/admin_web/src/assets/fonts/{family}-OFL.txt").read_bytes()) for family in ("Anton", "Manrope")}
        record = {
            "format_version": 1, "at": datetime.now(UTC).isoformat(), "source_url": SOURCE,
            "selection": "04 · 青柠 / Lime", "source_bytes": len(source), "source_sha256": digest(source),
            "files": {path.relative_to(ROOT).as_posix(): {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())} for path in (DEST / "lime.css", DEST / "fonts.css", runtime_css, *font_files)},
            "css_section_unchanged": True, "font_declarations_unchanged": True, "fonts": font_records,
            "font_binaries_unchanged": True, "runtime_font_source": "self", "runtime_font_adaptation": "Replace only the data URL with the original decoded local WOFF to honor the existing CSP",
            "original_components_sha256": digest(components), "original_components_executed_by_pull": False,
            "full_original_source": ".local/design-reference/html-cafe-x5b381ea1.html",
            "original_component_source": ".local/design-reference/lime.components.js",
            "font_license": "SIL OFL 1.1", "font_license_files": licenses,
            "font_license_source_commit": "cee74d7ca45d66d5c1878c828645c862f2788d2d",
            "license_note": "Font licenses cover the font software. No broader license is asserted for the reference site.",
            "runtime_remote_requests_required": False,
        }
        LOCK.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = json.loads(LOCK.read_text(encoding="utf-8"))
    for name, expected in record["files"].items():
        content = (ROOT / name).read_bytes()
        if len(content) != expected["bytes"] or digest(content) != expected["sha256"]:
            raise ValueError("Pulled reference drift: " + name)
    for name, expected in record["font_license_files"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("Font license drift: " + name)
    print(json.dumps({"passed": True, "pulled": args.pull, "source": SOURCE, "original_css_and_fonts": True, "files": len(record["files"]), "fonts": len(record["fonts"]), "remote_code_executed": False}))


if __name__ == "__main__":
    main()
