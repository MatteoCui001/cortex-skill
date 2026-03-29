"""
Command router: maps WeChat messages to Cortex API calls.

Used by the OpenClaw agent to dispatch user inputs.

Usage as a script (called by OpenClaw):
    echo '{"text": "inbox"}' | python3 command_router.py
    python3 command_router.py "some text to ingest"

Reads connection config from ~/.cortex/skill_config.yaml.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SKILL_CONFIG_PATH = Path.home() / ".cortex" / "skill_config.yaml"


@dataclass
class CortexClient:
    """Minimal HTTP client for the Cortex API."""

    base_url: str = "http://127.0.0.1:8420/api/v1"
    workspace: str = "default"
    timeout: int = 15
    api_token: str = ""

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body else None
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        req = urllib.request.Request(
            url, data=data, method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def ingest_url(self, url: str, annotation: str = "") -> dict:
        body: dict = {
            "url": url,
            "source": "wechat",
            "workspace_id": self.workspace,
        }
        if annotation:
            body["user_annotation"] = annotation
        return self._request("POST", "/events/ingest", body)

    def ingest_text(self, text: str) -> dict:
        return self._request("POST", "/events/ingest", {
            "content": text,
            "source": "wechat",
            "raw_input_type": "text",
            "workspace_id": self.workspace,
        })

    def get_notifications(self, status: str = "") -> list[dict]:
        qs = f"?status={status}" if status else ""
        return self._request("GET", f"/notifications{qs}")

    def notification_action(self, nid: str, action: str) -> dict:
        return self._request("POST", f"/notifications/{nid}/{action}")

    def signal_feedback(self, signal_id: str, verdict: str, note: str = "") -> dict:
        body: dict = {"verdict": verdict}
        if note:
            body["note"] = note
        return self._request("POST", f"/signals/{signal_id}/feedback", body)

    def health(self) -> dict:
        return self._request("GET", "/health")


# URL regex
_URL_RE = re.compile(r"https?://\S+")

# Command patterns
_COMMANDS = {
    "inbox": "inbox",
    "收件箱": "inbox",
    "通知": "inbox",
}

_ACTION_RE = re.compile(
    r"^(read|ack|dismiss|useful|not_useful|wrong|save_for_later)\s+(\S+)$",
    re.IGNORECASE,
)


@dataclass
class RouterResult:
    action: str
    data: dict
    summary: str


def route(text: str, client: CortexClient) -> RouterResult:
    """Route a user message to the appropriate Cortex API call."""
    text = text.strip()

    # Check command keywords
    lower = text.lower()
    if lower in _COMMANDS:
        notifications = client.get_notifications()
        count = len(notifications)
        if count == 0:
            return RouterResult("inbox", {}, "No pending notifications.")
        lines = []
        for n in notifications[:10]:
            status = n.get("status", "?")
            nid = n["id"][:8]
            lines.append(f"[{status}] {nid} | {n['title']}")
        summary = f"{count} notification(s):\n" + "\n".join(lines)
        return RouterResult("inbox", {"notifications": notifications}, summary)

    # Check action commands (read/ack/dismiss/feedback)
    m = _ACTION_RE.match(text)
    if m:
        action, target_id = m.group(1).lower(), m.group(2)
        if action in ("read", "ack", "dismiss"):
            result = client.notification_action(target_id, action)
            return RouterResult(
                action, result,
                f"Notification {target_id[:8]} marked as {action}.",
            )
        else:
            # Signal feedback
            result = client.signal_feedback(target_id, action)
            return RouterResult(
                "feedback", result,
                f"Feedback '{action}' submitted for signal {target_id[:8]}.",
            )

    # Check for URL
    url_match = _URL_RE.search(text)
    if url_match:
        url = url_match.group(0)
        annotation = text.replace(url, "").strip()
        result = client.ingest_url(url, annotation)
        return RouterResult(
            "ingest_url", result,
            f"Link ingested: {result.get('title', url[:40])}",
        )

    # Default: ingest as text note
    result = client.ingest_text(text)
    return RouterResult(
        "ingest_text", result,
        f"Note saved: {result.get('title', text[:30])}",
    )


# ---------------------------------------------------------------------------
# Config loader + CLI entry point
# ---------------------------------------------------------------------------

def _load_skill_config() -> dict[str, str]:
    """Load skill_config.yaml into a flat dict (section.key -> value)."""
    if not SKILL_CONFIG_PATH.exists():
        return {}
    config: dict[str, str] = {}
    section = ""
    for line in SKILL_CONFIG_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        m = re.match(r"\s+(\w+):\s*(.*)", line)
        if m:
            key = f"{section}.{m.group(1)}" if section else m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            config[key] = val
    return config


def client_from_config() -> CortexClient:
    """Build a CortexClient from ~/.cortex/skill_config.yaml."""
    cfg = _load_skill_config()
    return CortexClient(
        base_url=cfg.get("cortex.base_url", "http://127.0.0.1:8420/api/v1"),
        workspace=cfg.get("cortex.workspace", "default"),
        api_token=cfg.get("cortex.api_token", ""),
    )


def main() -> int:
    """CLI entry point — read message from argv or stdin, route, print JSON."""
    # Get message text
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        raw = sys.stdin.read().strip()
        if raw.startswith("{"):
            payload = json.loads(raw)
            text = payload.get("text", "")
        else:
            text = raw

    if not text:
        print(json.dumps({"error": "no input text"}))
        return 1

    client = client_from_config()
    try:
        result = route(text, client)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps({
        "action": result.action,
        "summary": result.summary,
        "data": result.data,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
