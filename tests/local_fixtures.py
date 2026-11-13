"""Optional private maintainer fixture location; never used by the server."""
from pathlib import Path

CALIBRATION = Path(__file__).resolve().parents[1] / 'calibration/10-terminal-wire.pdsprj'
