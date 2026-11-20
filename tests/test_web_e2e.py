"""Real-browser click-through of the BoardUI front end (kiosk and coordinator) against a real service."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = Path.home() / ".cache" / "ms-playwright"
BROWSER_OVERRIDE = os.environ.get("E2E_BROWSER")
WINDOWS_CHROME = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
BROWSER_AVAILABLE = (
    Path(BROWSER_OVERRIDE).is_file() if BROWSER_OVERRIDE else
    WINDOWS_CHROME.is_file() if os.name == "nt" else
    any(BROWSERS.glob("chromium_headless_shell*"))
)


@pytest.mark.skipif(not (ROOT / "web" / "out").is_dir(), reason="front end not built (npm --prefix web run build)")
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not BROWSER_AVAILABLE, reason="no configured Chromium browser")
@pytest.mark.skipif(not (ROOT / "web" / "node_modules" / "playwright-core").is_dir(), reason="npm --prefix web install not run")
def test_kiosk_and_coordinator_click_through():
    run = subprocess.run(["node", "web/e2e/run.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=280)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.count("PASS") >= 12 and "FAIL" not in run.stdout
