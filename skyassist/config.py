import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from skyassist.env import load_dotenv

load_dotenv()  # project-root .env, e.g. DEEPGRAM_API_KEY; real environment variables win

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class Mode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


@dataclass(frozen=True)
class Config:
    mode: Mode
    db_path: Path


PROFILES = {
    Mode.DEMO: Config(Mode.DEMO, DATA_DIR / "skyassist-demo.sqlite3"),
    Mode.LIVE: Config(Mode.LIVE, DATA_DIR / "skyassist-live.sqlite3"),
}
_configured = PROFILES[Mode(os.environ.get("SKYASSIST_MODE", Mode.DEMO))]
CONFIG = Config(_configured.mode, Path(os.environ.get("SKYASSIST_DB_PATH") or _configured.db_path).expanduser())
