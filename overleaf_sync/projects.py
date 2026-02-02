import os
import re
from typing import Dict

SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def folder_name_for(project_name: str | None, project_id: str) -> str:
    if not project_name:
        return project_id
    base = SAFE_CHARS.sub("-", project_name).strip("-._")
    suffix = project_id[:8] if project_id else ""
    name = f"{base}-{suffix}" if suffix else base
    return name or (project_id or "overleaf-project")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
