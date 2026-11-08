"""M7 entry point: `uv run python eval/run_eval.py <run_id> [--models a:b,c:d]`.

Same as `uv run leadgraph eval <run_id>`; kept here because the build plan names this file.
"""

import sys

from leadgraph.cli import app

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "eval", *sys.argv[1:]]
    app()
