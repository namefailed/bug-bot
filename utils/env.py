"""
Tiny .env loader shared by the orchestrator and the dashboard so both processes
pick up GITHUB_TOKEN (and anything else) from the same .env file.
"""

import os


def load_env_file(path: str = ".env"):
    """Load KEY=VALUE lines from a .env file into os.environ (overriding)."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
    except Exception:
        # Never let a malformed .env crash startup.
        pass
