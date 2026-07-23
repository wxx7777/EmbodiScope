import os
from pathlib import Path

from embodiscope.server import run_server


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    host = os.environ.get("EMBODISCOPE_HOST", "127.0.0.1")
    port = int(os.environ.get("EMBODISCOPE_PORT", "8765"))
    run_server(root, root / "data" / "demo_pick_place.csv", host=host, port=port)
