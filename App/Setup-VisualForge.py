"""Entry point of the component installer (started by Setup-VisualForge.cmd; works with any Python 3.9+)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'server'))
from provision.setup_cli import main  # noqa: E402

if __name__ == '__main__':
    main()
