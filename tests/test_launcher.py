"""Offline checks for run_demo.sh and the operator documents. Nothing here starts a server."""

import os
import subprocess
from pathlib import Path

import pytest

from skyassist.config import PROFILES, Mode

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_demo.sh"
DOCS = [
    ROOT / "run_demo.sh",
    ROOT / "README.md",
    ROOT / "docs" / "OPERATOR-CHECKLIST.md",
    ROOT / "docs" / "DEMO-SCRIPT.md",
]
# Em dash and the other dash code points the punctuation rule bans.
BANNED_DASHES = (chr(0x2014), chr(0x2015))
# The retired Streamlit UI and its ports must not appear in the launcher or the operator documents.
RETIRED = ("streamlit", "8501", "8502")


def launcher(*args, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True, timeout=30, env=env,
    )


def test_launcher_is_valid_bash_and_executable():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


def test_up_dry_run_starts_one_process_on_the_api_port():
    result = launcher("up", "--dry-run")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "uvicorn skyassist.service_read:app_from_env --factory --host 127.0.0.1 --port 8000" in out
    background = [line for line in out.splitlines() if line.startswith("+ ") and line.endswith(" &")]
    assert len(background) == 1
    assert "uv sync --frozen --python 3.14" in out


def test_up_dry_run_prints_the_kiosk_and_coordinator_urls():
    out = launcher("up", "--dry-run").stdout
    assert "http://127.0.0.1:8000/" in out
    assert "http://127.0.0.1:8000/staff/#token=demo-coordinator" in out
    assert "http://127.0.0.1:8000/clock" in out


def test_coordinator_token_from_env_is_never_printed():
    env = {**os.environ, "SKYASSIST_COORDINATOR_TOKEN": "not-a-demo-secret-42"}
    result = launcher("up", "--dry-run", env=env)
    assert result.returncode == 0, result.stderr
    assert "not-a-demo-secret-42" not in result.stdout + result.stderr
    assert "#token=" not in result.stdout
    assert "SKYASSIST_COORDINATOR_TOKEN" in result.stdout


def test_up_dry_run_names_the_demo_database_from_config():
    out = launcher("up", "--dry-run").stdout
    db = PROFILES[Mode.DEMO].db_path
    posix = db.as_posix()
    shell_path = f"/{db.drive[0].lower()}{posix[2:]}" if db.drive else posix
    assert posix in out or shell_path in out


def test_reset_dry_run_rebuilds_through_build_demo_db():
    result = launcher("reset", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "from skyassist.seed import build_demo_db" in result.stdout
    assert "uvicorn" not in result.stdout


def test_unknown_subcommand_exits_nonzero():
    result = launcher("bogus")
    assert result.returncode != 0
    assert "unknown subcommand" in result.stderr


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_retired_streamlit_references(path):
    text = path.read_text(encoding="utf-8").lower()
    for word in RETIRED:
        assert word not in text, f"{path.name} still mentions {word}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_no_dash_punctuation(path):
    assert path.exists(), f"missing {path.relative_to(ROOT)}"
    text = path.read_text(encoding="utf-8")
    for dash in BANNED_DASHES:
        assert dash not in text, f"{path.name} contains U+{ord(dash):04X}"
