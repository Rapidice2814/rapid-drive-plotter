"""Compatibility entry point; the application is implemented in debugger_main."""

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from USB_debugger.debugger_main import main


if __name__ == "__main__":
    raise SystemExit(main())

