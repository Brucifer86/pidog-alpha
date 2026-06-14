"""PiDog MCP Server — FastMCP wrapper over the PiDog REST API.

Exposes PiDog capabilities as MCP tools over SSE transport.
Talks to the PiDog FastAPI service via HTTP with API key auth.

Configuration (environment variables):
    PIDOG_MCP_URL        Base URL of the PiDog API (default: http://localhost:8000)
    PIDOG_MCP_API_KEY    API key or access token for authentication (required)
    PIDOG_MCP_HOST       MCP server bind address (default: 0.0.0.0)
    PIDOG_MCP_PORT       MCP server port (default: 8100)
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PIDOG_URL = os.getenv("PIDOG_MCP_URL", "http://localhost:8000").rstrip("/")
PIDOG_API_KEY = os.getenv("PIDOG_MCP_API_KEY", "")
MCP_HOST = os.getenv("PIDOG_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("PIDOG_MCP_PORT", "8100"))

mcp = FastMCP(
    "pidog",
    instructions=(
        "PiDog is a robot dog you can control. Use these tools to move, "
        "pose, express emotions, play sounds, control LEDs, take camera "
        "snapshots, and check status. Actions are queued and executed one "
        "at a time by the robot."
    ),
)

# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if PIDOG_API_KEY:
        h["Authorization"] = f"Bearer {PIDOG_API_KEY}"
    return h


def _client() -> httpx.Client:
    return httpx.Client(base_url=PIDOG_URL, headers=_headers(), timeout=30.0)


def _post(path: str, json: dict | None = None) -> dict[str, Any]:
    with _client() as c:
        r = c.post(path, json=json or {})
        r.raise_for_status()
        return r.json()


def _get(path: str) -> dict[str, Any]:
    with _client() as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _get_bytes(path: str) -> bytes:
    with _client() as c:
        r = c.get(path)
        r.raise_for_status()
        return r.content


def _delete(path: str) -> dict[str, Any]:
    with _client() as c:
        r = c.delete(path)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Movement tools
# ---------------------------------------------------------------------------


@mcp.tool()
def move(
    direction: str,
    speed: int = 80,
    step_count: int = 1,
) -> dict[str, Any]:
    """Move PiDog in a direction.

    Args:
        direction: One of "forward", "backward", "turn_left", "turn_right", "trot".
        speed: Motor speed 1-100 (default 80).
        step_count: Number of gait cycles 1-10 (default 1).
    """
    valid = {"forward", "backward", "turn_left", "turn_right", "trot"}
    if direction not in valid:
        return {"error": f"Invalid direction. Choose from: {', '.join(sorted(valid))}"}
    return _post("/actions/run", {"name": direction, "speed": speed, "step_count": step_count})


# ---------------------------------------------------------------------------
# Pose tools
# ---------------------------------------------------------------------------


@mcp.tool()
def pose(
    position: str,
    speed: int = 80,
) -> dict[str, Any]:
    """Put PiDog into a body position.

    Args:
        position: One of "stand", "sit", "lie", "half_sit", "lie_with_hands_out",
                  "stretch", "push_up".
        speed: Motor speed 1-100 (default 80).
    """
    valid = {"stand", "sit", "lie", "half_sit", "lie_with_hands_out", "stretch", "push_up"}
    if position not in valid:
        return {"error": f"Invalid position. Choose from: {', '.join(sorted(valid))}"}
    return _post("/actions/run", {"name": position, "speed": speed})


# ---------------------------------------------------------------------------
# Expression / emote tools
# ---------------------------------------------------------------------------


@mcp.tool()
def emote(
    expression: str,
    speed: int = 80,
    step_count: int = 1,
) -> dict[str, Any]:
    """Make PiDog express an emotion or perform a gesture.

    Args:
        expression: One of "wag_tail", "shake_head", "tilting_head",
                    "tilting_head_left", "tilting_head_right", "head_up_down",
                    "head_bark", "doze_off", "nod_lethargy", "bark", "bark_harder",
                    "pant", "scratch", "handshake", "high_five", "howling",
                    "body_twisting".
        speed: Motor speed 1-100 (default 80).
        step_count: Repetitions 1-10 (default 1).
    """
    valid = {
        "wag_tail", "shake_head", "tilting_head", "tilting_head_left",
        "tilting_head_right", "head_up_down", "head_bark", "doze_off",
        "nod_lethargy", "bark", "bark_harder", "pant", "scratch",
        "handshake", "high_five", "howling", "body_twisting",
    }
    if expression not in valid:
        return {"error": f"Invalid expression. Choose from: {', '.join(sorted(valid))}"}
    return _post("/actions/run", {"name": expression, "speed": speed, "step_count": step_count})


# ---------------------------------------------------------------------------
# Sound tools
# ---------------------------------------------------------------------------


@mcp.tool()
def play_sound(
    name: str,
    volume: int = 100,
) -> dict[str, Any]:
    """Play a sound on PiDog's speaker.

    Args:
        name: Sound name from the catalog (e.g. "angry", "howling", "woohoo",
              "single_bark_1", "pant", "snoring", etc.).
        volume: Playback volume 0-100 (default 100).
    """
    return _post("/sounds/play", {"name": name, "volume": volume})


@mcp.tool()
def list_sounds() -> dict[str, Any]:
    """List all available sounds in the PiDog catalog."""
    return _get("/sounds")


# ---------------------------------------------------------------------------
# LED tools
# ---------------------------------------------------------------------------


@mcp.tool()
def set_leds(
    style: str = "breath",
    color: str = "cyan",
    brightness: float = 1.0,
    bps: float = 1.0,
    runtime_seconds: float | None = None,
) -> dict[str, Any]:
    """Control PiDog's RGB LED strip.

    Args:
        style: LED animation style. One of "off", "monochromatic", "breath",
               "boom", "bark", "speak", "listen".
        color: LED color. One of "white", "black", "red", "yellow", "green",
               "blue", "cyan", "magenta", "pink".
        brightness: LED brightness 0.0-1.0 (default 1.0).
        bps: Animation beats per second 0.1-10.0 (default 1.0).
        runtime_seconds: Auto-off timer in seconds (optional, LEDs stay on if omitted).
    """
    payload: dict[str, Any] = {
        "style": style,
        "color": color,
        "brightness": brightness,
        "bps": bps,
    }
    if runtime_seconds is not None:
        payload["runtime_seconds"] = runtime_seconds
    return _post("/leds/set", payload)


# ---------------------------------------------------------------------------
# Camera tools
# ---------------------------------------------------------------------------


@mcp.tool()
def look() -> dict[str, Any]:
    """Take a camera snapshot from PiDog's perspective.

    Returns a base64-encoded JPEG image.
    """
    try:
        image_bytes = _get_bytes("/camera/snapshot")
        return {
            "ok": True,
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "content_type": "image/jpeg",
            "size_bytes": len(image_bytes),
        }
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Status & health tools
# ---------------------------------------------------------------------------


@mcp.tool()
def status() -> dict[str, Any]:
    """Get PiDog's current status including health, battery, and camera state."""
    return _get("/status")


@mcp.tool()
def health() -> dict[str, Any]:
    """Quick health check — controller mode, job queue, idle state, sensors."""
    return _get("/health")


# ---------------------------------------------------------------------------
# Catalog tool
# ---------------------------------------------------------------------------


@mcp.tool()
def catalog() -> dict[str, Any]:
    """List all available actions, sounds, LED styles, and LED colors."""
    return _get("/catalog")


# ---------------------------------------------------------------------------
# Job status tool
# ---------------------------------------------------------------------------


@mcp.tool()
def job_status(job_id: str) -> dict[str, Any]:
    """Check the status of an async PiDog job.

    Args:
        job_id: The job UUID returned from a previous command.
    """
    return _get(f"/jobs/{job_id}")


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


@mcp.tool()
def stop(lie_down: bool = False, speed: int = 80) -> dict[str, Any]:
    """Emergency stop — halt all PiDog motion immediately.

    Args:
        lie_down: If true, PiDog lies down after stopping (default false).
        speed: Speed for the lie-down motion 1-100 (default 80).
    """
    return _post("/stop", {"lie_down": lie_down, "speed": speed})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="sse", host=MCP_HOST, port=MCP_PORT)
