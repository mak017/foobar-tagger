"""Run the project Pyright configuration with the active Python interpreter."""

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Recheck when Python files change.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if importlib.util.find_spec("pyright") is None:
        print(
            "Pyright is not installed in this interpreter. Run:\n"
            f'  "{sys.executable}" -m pip install -r "{root / "requirements-dev.txt"}"',
            file=sys.stderr,
        )
        return 2
    command = [
        sys.executable, "-m", "pyright", "--project", str(root / "pyrightconfig.json"),
        "--pythonpath", sys.executable, "--warnings",
    ]
    if args.watch:
        command.append("--watch")
    try:
        return subprocess.call(command, cwd=root)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
