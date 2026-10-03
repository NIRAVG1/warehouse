"""Root conftest.py — adds the project root to sys.path so all test imports resolve
regardless of which Python interpreter or working directory pytest is invoked from."""
import sys
from pathlib import Path

# Always insert the project root at the front of the module search path.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
