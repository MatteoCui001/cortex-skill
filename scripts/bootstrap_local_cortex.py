#!/usr/bin/env python3
"""
Bootstrap a local Cortex installation on macOS.

Clones the repo, runs install.sh (which handles ALL dependencies),
reads the generated API token, and writes the skill connection config.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CORTEX_DIR = Path.home() / "Projects" / "cortex"
CORTEX_REPO = os.environ.get(
    "CORTEX_REPO", "https://github.com/MatteoCui001/cortex.git"
)
ENV_FILE = Path.home() / ".cortex" / "env"
SKILL_CONFIG_PATH = Path.home() / ".cortex" / "skill_config.yaml"
DEFAULT_API_PORT = 8420
DEFAULT_RELAY_PORT = 8421


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, printing it first."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def clone_or_update_repo() -> Path:
    """Clone Cortex repo or pull latest if it exists."""
    if CORTEX_DIR.is_dir() and (CORTEX_DIR / ".git").is_dir():
        print(f"\n=== Updating existing repo at {CORTEX_DIR} ===")
        _run(["git", "pull", "origin", "main"], cwd=CORTEX_DIR)
    else:
        print(f"\n=== Cloning Cortex to {CORTEX_DIR} ===")
        CORTEX_DIR.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", CORTEX_REPO, str(CORTEX_DIR)])
    return CORTEX_DIR


def run_installer(cortex_dir: Path) -> None:
    """Run install.sh which handles everything: deps, DB, migrations, console."""
    install_script = cortex_dir / "install.sh"
    if not install_script.is_file():
        print(f"  ERROR: {install_script} not found")
        sys.exit(1)

    print("\n=== Running Cortex installer ===")
    install_script.chmod(0o755)
    _run(["bash", str(install_script)], cwd=cortex_dir)


def read_api_token() -> str:
    """Read CORTEX_API_TOKEN from ~/.cortex/env."""
    if not ENV_FILE.is_file():
        return ""
    content = ENV_FILE.read_text()
    m = re.search(r'CORTEX_API_TOKEN="([^"]+)"', content)
    return m.group(1) if m else ""


def write_skill_config(api_token: str) -> None:
    """Write the skill connection config with API token."""
    print("\n=== Writing skill config ===")
    SKILL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = f"""\
cortex:
  base_url: http://127.0.0.1:{DEFAULT_API_PORT}/api/v1
  api_token: "{api_token}"
  workspace: default
openclaw:
  ingress_url: ""
relay:
  port: {DEFAULT_RELAY_PORT}
  enabled: false
"""
    SKILL_CONFIG_PATH.write_text(config)
    print(f"  Written to {SKILL_CONFIG_PATH}")


def main() -> int:
    print("Cortex Local Bootstrap (via install.sh)")
    print("=" * 45)

    # Only hard requirement: macOS + git (install.sh handles the rest)
    import platform
    if platform.system() != "Darwin":
        print("This bootstrap is designed for macOS only.")
        return 1

    if not shutil.which("git"):
        print("git not found. Install Xcode Command Line Tools:")
        print("  xcode-select --install")
        return 1

    # Clone or update repo
    cortex_dir = clone_or_update_repo()

    # Run install.sh (installs brew, pg, uv, python, pgvector, migrations, console)
    run_installer(cortex_dir)

    # Read the auto-generated API token
    api_token = read_api_token()
    if api_token:
        print(f"\n  API token found ({len(api_token)} chars)")
    else:
        print("\n  WARNING: No API token found in ~/.cortex/env")

    # Write skill config with token
    write_skill_config(api_token)

    print("\n=== Bootstrap complete! ===")
    print(f"  Cortex: {cortex_dir}")
    print(f"  Config: {SKILL_CONFIG_PATH}")
    print(f"  API:    http://127.0.0.1:{DEFAULT_API_PORT}/api/v1")
    print(f"  Console: http://127.0.0.1:{DEFAULT_API_PORT}/console/")
    print("\nStart Cortex with:")
    print(f"  cd {cortex_dir} && source ~/.cortex/env && uv run cortex serve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
