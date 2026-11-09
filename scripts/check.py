"""Run CI's lint and test jobs locally. Keep in step with .github/workflows/ci.yml."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
STEPS = [
    ["ruff", "format", "--check", "."],
    ["ruff", "check", "."],
    ["ty", "check"],
    ["python", "release.py", "check"],
    ["pytest"],
    # The oldest Python the library supports. ty and ruff check its syntax, but not that an
    # annotation evaluated at import names something defined then.
    ["--isolated", "--python", "3.12", "pytest"],
]


def main() -> int:
    for step in STEPS:
        print(f"$ uv run {' '.join(step)}", flush=True)
        if subprocess.run(["uv", "run", *step], cwd=ROOT).returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
