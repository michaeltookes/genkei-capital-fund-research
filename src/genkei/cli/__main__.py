"""Enable ``python3 -m genkei.cli`` as an alternative to the ``genkei``
console-script entry point.

Useful when the package is importable but the console script isn't on
PATH — e.g. inline shell commands like
``python3 -m genkei.cli query "SELECT 1"`` against an editable-install
venv. The console-script form (``genkei query "SELECT 1"``) works the
same way; this just removes the "no module named genkei.cli.__main__"
papercut surfaced in the first live /research session.
"""

from __future__ import annotations

import sys

from genkei.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
