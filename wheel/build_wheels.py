"""Package the compiled agent into one Python wheel per platform.

This follows go-to-wheel (https://github.com/simonw/go-to-wheel): a platform-tagged wheel holds
the native executable under `pi_briefing/bin/` plus a thin Python package that finds and runs it.
go-to-wheel cross-compiles Go; here `agent/build.ts` cross-compiles the TypeScript agent with
`bun build --compile`, and this script only assembles the wheels.

    uv run --no-project python wheel/build_wheels.py           # every platform built in agent/dist
    uv run --no-project python wheel/build_wheels.py linux-x64   # some platforms
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIST = ROOT / "agent" / "dist"
PACKAGE_SOURCE = ROOT / "python" / "pi_briefing"
OUTPUT = ROOT / "dist" / "wheels"
NAME = "pi_briefing"
SUMMARY = "A daily GitHub briefing agent built on pi-durable, as a native executable"

# Tags match each binary's real floor: glibc 2.17 on Linux, macOS 13.0 (checked with
# `strings | grep GLIBC_` and `otool -l | grep minos`).
PLATFORM_TAGS = {
    "linux-x64": "manylinux_2_17_x86_64",
    "linux-arm64": "manylinux_2_17_aarch64",
    "darwin-arm64": "macosx_13_0_arm64",
    "darwin-x64": "macosx_13_0_x86_64",
}

EXECUTABLE = (stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH) << 16
REGULAR = (stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH) << 16


def package_version() -> str:
    """The agent's version, after checking that the Python package declares the same one."""
    version = json.loads((ROOT / "agent" / "package.json").read_text())["version"]
    match = re.search(
        r'^__version__ = "([^"]+)"$', (PACKAGE_SOURCE / "__init__.py").read_text(), re.M
    )
    if match is None or match.group(1) != version:
        found = match.group(1) if match else "nothing"
        raise SystemExit(
            f"python/pi_briefing has __version__ {found}; agent/package.json has {version}"
        )
    return version


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={digest.decode()}"


def dist_info(version: str, tag: str) -> dict[str, bytes]:
    info = f"{NAME}-{version}.dist-info"
    metadata = "\n".join(
        [
            "Metadata-Version: 2.1",
            f"Name: {NAME.replace('_', '-')}",
            f"Version: {version}",
            f"Summary: {SUMMARY}",
            "License: MIT",
            "Requires-Python: >=3.11",
            "",
        ]
    )
    wheel = (
        "Wheel-Version: 1.0\nGenerator: build_wheels.py\nRoot-Is-Purelib: false\n"
        f"Tag: py3-none-{tag}\n"
    )
    entry_points = f"[console_scripts]\npi-briefing = {NAME}:main\n"
    return {
        f"{info}/METADATA": metadata.encode(),
        f"{info}/WHEEL": wheel.encode(),
        f"{info}/entry_points.txt": entry_points.encode(),
    }


def build_wheel(platform: str, version: str) -> Path:
    binary = AGENT_DIST / platform / "pi-briefing"
    if not binary.exists():
        raise SystemExit(f"{binary} is missing; run `bun run build.ts {platform}` in agent/ first")
    tag = PLATFORM_TAGS[platform]
    files = {
        f"{NAME}/{source.name}": source.read_bytes()
        for source in sorted(PACKAGE_SOURCE.glob("*.py"))
    }
    files[f"{NAME}/bin/pi-briefing"] = binary.read_bytes()
    files.update(dist_info(version, tag))
    record_path = f"{NAME}-{version}.dist-info/RECORD"
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, data in files.items():
        writer.writerow([path, record_hash(data), len(data)])
    writer.writerow([record_path, "", ""])
    files[record_path] = record.getvalue().encode()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    wheel_path = OUTPUT / f"{NAME}-{version}-py3-none-{tag}.whl"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for path, data in files.items():
            entry = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = EXECUTABLE if path.endswith("/bin/pi-briefing") else REGULAR
            wheel.writestr(entry, data)
    return wheel_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("platforms", nargs="*", help=f"any of {', '.join(PLATFORM_TAGS)}")
    platforms = parser.parse_args().platforms or [
        p for p in PLATFORM_TAGS if (AGENT_DIST / p).exists()
    ]
    unknown = sorted(set(platforms) - set(PLATFORM_TAGS))
    if unknown:
        parser.error(
            f"unknown platform {', '.join(unknown)}; use any of {', '.join(PLATFORM_TAGS)}"
        )
    if not platforms:
        raise SystemExit("No compiled agent found; run `bun run build.ts` in agent/ first")
    version = package_version()
    for platform in platforms:
        path = build_wheel(platform, version)
        print(f"{path.relative_to(ROOT)}  ({path.stat().st_size / 1e6:.1f} MB)", file=sys.stdout)


if __name__ == "__main__":
    main()
