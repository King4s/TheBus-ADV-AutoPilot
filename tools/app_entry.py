"""Entry point for the packaged 'TheBus Copilot' app (PyInstaller build).

--selftest builds the window and exits after 2 s (used by the build check).
"""
import sys

from thebus_ai_bridge.gui import main

if __name__ == "__main__":
    main(selftest="--selftest" in sys.argv)
