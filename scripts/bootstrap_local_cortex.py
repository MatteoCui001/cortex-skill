#!/usr/bin/env python3
"""
Bootstrap a local Cortex installation on macOS.

Clones the repo, runs install.sh, reads the generated API token,
writes the skill connection config, creates a launchd plist for
auto-start, and starts the service.

After this script finishes, Cortex is running and ready to use.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CORTEX_DIR = Path.home() / "Projects" / "cortex"
CORTEX_REPO = os.environ.get(
    "CORTEX_REPO", "https://github.com/MatteoCui001/cortex.git"
)
ENV_FILE = Path.home() / ".cortex" / "env"
SKILL_CONFIG_PATH = Path.home() / ".cortex" / "skill_config.yaml"
PLIST_LABEL = "com.cortex.serve"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
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


def _find_uv() -> str:
    """Find the uv binary path."""
    uv = shutil.which("uv")
    if uv:
        return uv
    # Common homebrew / cargo locations
    for candidate in [
        Path.home() / ".cargo" / "bin" / "uv",
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
    ]:
        if candidate.is_file():
            return str(candidate)
    return "uv"  # hope it's on PATH at launchd time


def write_launchd_plist(cortex_dir: Path) -> None:
    """Create a launchd plist so Cortex starts automatically on login."""
    print("\n=== Creating launchd plist ===")
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    uv_bin = _find_uv()

    # Build environment variables from ~/.cortex/env
    env_dict: dict[str, str] = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            m = re.match(r'^export\s+(\w+)="([^"]*)"', line)
            if m:
                env_dict[m.group(1)] = m.group(2)

    env_xml = "\n".join(
        f"      <key>{k}</key>\n      <string>{v}</string>"
        for k, v in env_dict.items()
    )

    plist_content = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{uv_bin}</string>
    <string>run</string>
    <string>cortex</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{cortex_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{Path.home() / ".cortex" / "cortex.log"}</string>
  <key>StandardErrorPath</key>
  <string>{Path.home() / ".cortex" / "cortex.log"}</string>
</dict>
</plist>
"""
    PLIST_PATH.write_text(plist_content)
    print(f"  Written to {PLIST_PATH}")


def start_service() -> None:
    """Load (or reload) the launchd plist to start Cortex."""
    print("\n=== Starting Cortex service ===")
    # Unload first if already loaded (ignore errors)
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )
    _run(["launchctl", "load", str(PLIST_PATH)])
    print("  Service loaded via launchd")


def wait_for_health(timeout: int = 30) -> bool:
    """Wait until /health returns ok or timeout."""
    print(f"\n=== Waiting for Cortex to be ready (up to {timeout}s) ===")
    url = f"http://127.0.0.1:{DEFAULT_API_PORT}/api/v1/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "ok":
                    print(f"  Cortex is healthy: {data}")
                    return True
        except Exception:
            pass
        time.sleep(2)
    print("  WARNING: Cortex did not become healthy within timeout")
    return False


def main() -> int:
    print("Cortex Local Bootstrap")
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

    # 1. Clone or update repo
    cortex_dir = clone_or_update_repo()

    # 2. Run install.sh (installs brew, pg, uv, python, pgvector, migrations, console)
    run_installer(cortex_dir)

    # 3. Read the auto-generated API token
    api_token = read_api_token()
    if api_token:
        print(f"\n  API token found ({len(api_token)} chars)")
    else:
        print("\n  WARNING: No API token found in ~/.cortex/env")

    # 4. Write skill config with token
    write_skill_config(api_token)

    # 5. Create launchd plist (auto-start on login + keep alive)
    write_launchd_plist(cortex_dir)

    # 6. Start the service now
    start_service()

    # 7. Wait for health
    healthy = wait_for_health()

    print("\n=== Bootstrap complete! ===")
    print(f"  Cortex:  {cortex_dir}")
    print(f"  Config:  {SKILL_CONFIG_PATH}")
    print(f"  API:     http://127.0.0.1:{DEFAULT_API_PORT}/api/v1")
    print(f"  Console: http://127.0.0.1:{DEFAULT_API_PORT}/console/")
    print(f"  Logs:    ~/.cortex/cortex.log")
    print(f"  Status:  {'healthy' if healthy else 'starting...'}")
    print(f"\n  Cortex will auto-start on login (launchd).")
    print(f"  To stop:  launchctl unload {PLIST_PATH}")
    print(f"  To start: launchctl load {PLIST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
