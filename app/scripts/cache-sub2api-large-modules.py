"""Prefetch two pinned public module ZIPs; native Go still verifies go.sum."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PROXY = "https://goproxy.cn/"
MODULES = (("github.com/klauspost/compress", "v1.18.2", 39191865), ("github.com/gabriel-vasile/mimetype", "v1.4.2", 25157147))
BLOCK = 256 << 10


def main() -> None:
    started = time.monotonic()
    target = ROOT / ".local/research/go-proxy"
    for module, version, size in MODULES:
        directory = target / module / "@v"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / (version + ".zip")
        ranges = [(offset, min(offset + BLOCK, size) - 1) for offset in range(0, size, BLOCK)]

        def fetch(bounds: tuple[int, int], module_name: str = module, module_version: str = version, module_size: int = size, block_directory: Path = directory) -> bytes:
            if time.monotonic() - started > 300:
                raise TimeoutError("Module prefetch deadline reached")
            first, last = bounds
            part = block_directory / f"{module_version}.{first}-{last}.part"
            if part.exists() and part.stat().st_size == last - first + 1:
                return part.read_bytes()
            request = Request(PROXY + module_name + "/@v/" + module_version + ".zip", headers={"Range": f"bytes={first}-{last}"})  # noqa: S310 -- fixed HTTPS proxy and allowlisted modules
            with urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed HTTPS proxy and allowlisted module paths
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {first}-{last}/{module_size}":
                    raise RuntimeError("Module proxy did not honor the exact byte range")
                data = bytearray()
                while len(data) < last - first + 1:
                    if time.monotonic() - started > 300:
                        raise TimeoutError("Module prefetch deadline reached")
                    chunk = response.read1(min(32768, last - first + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            if len(data) != last - first + 1:
                raise RuntimeError("Incomplete module block")
            part.write_bytes(data)
            return bytes(data)

        if not output.exists() or output.stat().st_size != size:
            temporary = directory / (version + ".zip.partial")
            with temporary.open("wb") as destination, ThreadPoolExecutor(max_workers=16) as pool:
                for position, block in enumerate(pool.map(fetch, ranges), 1):
                    destination.write(block)
                    if position % 32 == 0:
                        print(json.dumps({"module": module, "cached_bytes": destination.tell(), "expected_bytes": size}), flush=True)
            if temporary.stat().st_size != size:
                raise RuntimeError("Module ZIP length mismatch")
            temporary.replace(output)
        for extension in ("mod", "info"):
            with urlopen(PROXY + module + "/@v/" + version + "." + extension, timeout=15) as response:  # noqa: S310 -- fixed allowlisted HTTPS origin and module
                data = response.read(262145)
            if len(data) > 262144:
                raise RuntimeError("Module metadata exceeds the byte limit")
            (directory / (version + "." + extension)).write_bytes(data)
        print(json.dumps({"module": module, "version": version, "bytes": size, "download_cached": True, "go_sum_verification_pending": True}), flush=True)


if __name__ == "__main__":
    main()
