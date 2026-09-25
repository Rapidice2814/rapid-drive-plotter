"""Launch the USB debugger application from the project root."""

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT / "USB_debugger"))

from USB_debugger.debugger_main import main


if __name__ == "__main__":
    raise SystemExit(main())
