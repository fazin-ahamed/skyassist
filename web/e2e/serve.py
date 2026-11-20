"""Start the integrated service on a seeded temp DB with the C01 replay clock (used by e2e/run.mjs)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn

from skyassist import seed
from skyassist.clock import SimClock
from skyassist.config import Config, Mode
from skyassist.service_read import create_integrated_app

db = seed.build_demo_db(Path(tempfile.mkdtemp()) / "demo.sqlite3")
app = create_integrated_app(SimClock("C01"), Config(Mode.DEMO, Path(db)))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
