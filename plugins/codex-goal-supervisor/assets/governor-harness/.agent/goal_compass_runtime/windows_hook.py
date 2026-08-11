"""Fixed Windows hook launcher; avoids cmd.exe quoting of inline Python."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    project_hook = project_root / ".agent" / "goal_compass_runtime" / "project_hook.py"
    compass = project_root / ".agent" / "goal_compass.py"
    os.chdir(project_root)
    sys.path.insert(0, str(project_root / ".agent"))
    target = project_hook if project_hook.is_file() else compass
    sys.argv = [str(target)] if target == project_hook else [str(target), "hook"]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
