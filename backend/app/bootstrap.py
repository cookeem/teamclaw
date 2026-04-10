from __future__ import annotations

import importlib.util
from pathlib import Path


def _find_project_root(current: Path) -> Path:
    """Find TeamClaw project root by layout markers."""
    for parent in current.parents:
        if (parent / "config.yaml").is_file() and (parent / "backend").is_dir():
            return parent
    # Fallback for typical backend/app/bootstrap.py layout.
    return current.parents[2]


def bootstrap_deepagents_paths() -> Path:
    """Validate DeepAgents pip dependencies and return project root.

    Returns:
        The repository root path.
    """
    current = Path(__file__).resolve()
    repo_root = _find_project_root(current)
    missing = [
        package
        for package in ("deepagents", "deepagents_cli")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        package_names = ", ".join(missing)
        msg = (
            f"Missing required pip package(s): {package_names}.\n"
            "Please install backend dependencies:\n"
            "  pip install -r backend/requirements.txt"
        )
        raise RuntimeError(msg)

    return repo_root
