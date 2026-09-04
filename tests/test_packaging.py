"""What a built distribution actually contains.

The next work item is the Docker runtime, which installs HELIOS rather than
running it from a source checkout. Two things must therefore be true of a
BUILT distribution, and neither is provable from the source tree:

* the reference strategy packages under ``helios/strategies/packages/`` ship
  with the code. They are data, and ``[tool.setuptools.packages.find]``
  discovers importable packages only, so without an explicit ``package-data``
  declaration the YAML files are silently absent from the built artefact;
* ``helios.strategies.catalogue.REFERENCE_PACKAGE_DIRECTORY`` resolves in an
  installed layout, not merely relative to the repository root.

So this builds a distribution into a temporary directory and loads the
reference packages back out of it in a FRESH interpreter that cannot see the
source tree at all. Nothing here reaches the network: the build runs the
already-installed backend in-process, with no isolation and no dependency
resolution.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

from helios.strategies.catalogue import REFERENCE_PACKAGE_DIRECTORY

#: Every file the built distribution must carry, as a distribution-relative path.
def shipped_data_files() -> list[str]:
    return sorted(
        f"helios/strategies/packages/{path.name}"
        for path in REFERENCE_PACKAGE_DIRECTORY.glob("*.yaml")
    )


@pytest.fixture(scope="module")
def built_distribution(tmp_path_factory, request) -> Path:
    """Build HELIOS from a copy of the source tree; return the built archive."""
    root = request.config.rootpath
    workspace = tmp_path_factory.mktemp("distribution")
    source = workspace / "source"
    source.mkdir()
    shutil.copy(root / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        root / "helios",
        source / "helios",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    output = workspace / "output"
    output.mkdir()
    probe = (
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from setuptools import build_meta\n"
        "sys.stdout.write(build_meta.build_wheel(sys.argv[1]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(output)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(f"building the distribution failed:\n{completed.stderr}")
    return output / completed.stdout.strip().splitlines()[-1]


def test_the_reference_strategy_packages_are_in_the_built_distribution(
    built_distribution,
):
    """Without the package-data declaration this archive holds no YAML at all."""
    expected = shipped_data_files()
    assert expected, "the reference strategy packages moved; update this check"
    with zipfile.ZipFile(built_distribution) as archive:
        names = set(archive.namelist())
    missing = [name for name in expected if name not in names]
    assert not missing, f"absent from the built distribution: {missing}"


def test_the_reference_packages_load_from_an_installed_layout(
    built_distribution, tmp_path
):
    """Proof by installation: a fresh interpreter that cannot see the source.

    ``REFERENCE_PACKAGE_DIRECTORY`` is resolved from the installed module's own
    location, so this fails if the data did not ship — which is precisely the
    Docker failure it exists to prevent.
    """
    installed = tmp_path / "installed"
    with zipfile.ZipFile(built_distribution) as archive:
        archive.extractall(installed)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    probe = (
        "import json\n"
        "from helios.spec import load_strategy_packages\n"
        "from helios.strategies.catalogue import REFERENCE_PACKAGE_DIRECTORY\n"
        "directory = REFERENCE_PACKAGE_DIRECTORY\n"
        "assert directory.is_dir(), directory\n"
        "packages = load_strategy_packages(directory)\n"
        "print(json.dumps({'root': str(directory), 'held': sorted(packages)}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": f"{installed}:{sysconfig.get_paths()['purelib']}",
            "PATH": "/usr/bin:/bin",
        },
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert str(installed) in result["root"]
    assert result["held"] == [
        "momentum_volatility@1.0.0",
        "no_wick_candle@1.0.0",
        "swing_proximity@1.0.0",
    ]
