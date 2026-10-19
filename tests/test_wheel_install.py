"""The built wheels, as installers see them.

Posit Connect Cloud installs with pip as one user and runs the app as another, so the app cannot
fix a missing execute bit. These tests check the bit without going through `binary_path()`,
which would otherwise repair it and hide the problem.
"""

import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

import pi_briefing

WHEELS = Path(__file__).resolve().parent.parent / "dist" / "wheels"


def built_wheels() -> list[Path]:
    wheels = sorted(WHEELS.glob(f"pi_briefing-{pi_briefing.__version__}-*.whl"))
    if not wheels:
        pytest.skip("no wheels built; run `make wheels`")
    return wheels


def test_every_entry_is_a_regular_file_and_only_the_executable_can_run() -> None:
    for wheel in built_wheels():
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                mode = entry.external_attr >> 16
                assert stat.S_ISREG(mode), f"{wheel.name}: {entry.filename} is not a regular file"
                runnable = bool(mode & 0o111)
                assert runnable == entry.filename.endswith("/bin/pi-briefing"), entry.filename


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to make a venv with pip")
def test_pip_installs_the_executable_with_its_execute_bit(tmp_path: Path) -> None:
    built_wheels()
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--seed", "--quiet", str(venv)], check=True)
    pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--no-index"]
    pip += ["--find-links", str(WHEELS), f"pi-briefing=={pi_briefing.__version__}"]
    subprocess.run(pip, check=True)
    (binary,) = venv.glob("lib/python*/site-packages/pi_briefing/bin/pi-briefing")
    assert binary.stat().st_mode & 0o111 == 0o111
