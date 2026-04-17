from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import logging
import os
import shlex
import shutil
import subprocess


logger = logging.getLogger(__name__)

class CameraError(RuntimeError):
    """Raised when the camera backend cannot satisfy a snapshot request."""


def _jpeg_looks_valid(payload: bytes) -> bool:
    return payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")


@dataclass
class CameraSettings:
    command_override: Optional[str] = None
    timeout_ms: int = 3000
    width: int = 1280
    height: int = 720
    quality: int = 90

    @classmethod
    def from_env(cls) -> "CameraSettings":
        return cls(
            command_override=os.getenv("PIDOG_CAMERA_COMMAND", "").strip() or None,
            timeout_ms=int(os.getenv("PIDOG_CAMERA_TIMEOUT_MS", "3000")),
            width=int(os.getenv("PIDOG_CAMERA_WIDTH", "1280")),
            height=int(os.getenv("PIDOG_CAMERA_HEIGHT", "720")),
            quality=int(os.getenv("PIDOG_CAMERA_QUALITY", "90")),
        )


class BaseCameraService:
    mode = "base"

    def __init__(self, settings: CameraSettings):
        self.settings = settings

    def status(self) -> Dict[str, object]:
        raise NotImplementedError

    def snapshot(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        return None


class MockCameraService(BaseCameraService):
    mode = "mock"

    def status(self) -> Dict[str, object]:
        return {
            "mode": self.mode,
            "available": False,
            "detail": "Camera snapshots are unavailable in mock mode.",
        }

    def snapshot(self) -> bytes:
        raise CameraError("Camera snapshots are unavailable in mock mode.")


class CommandCameraService(BaseCameraService):
    mode = "real"

    def __init__(self, settings: CameraSettings):
        super().__init__(settings=settings)
        self._last_backend: Optional[str] = None
        self._last_error: Optional[str] = None

    def _candidate_commands(self) -> List[List[str]]:
        if self.settings.command_override:
            return [shlex.split(self.settings.command_override)]

        timeout_ms = str(max(1, self.settings.timeout_ms))
        width = str(max(1, self.settings.width))
        height = str(max(1, self.settings.height))
        quality = str(min(100, max(1, self.settings.quality)))
        return [
            ["rpicam-jpeg", "--nopreview", "--timeout", timeout_ms, "--width", width, "--height", height, "--quality", quality, "-o", "-"],
            ["libcamera-jpeg", "-n", "-t", timeout_ms, "--width", width, "--height", height, "--quality", quality, "-o", "-"],
            ["libcamera-still", "-n", "-t", timeout_ms, "--width", width, "--height", height, "--quality", quality, "--encoding", "jpg", "-o", "-"],
        ]

    def _available_commands(self) -> List[str]:
        available: List[str] = []
        for command in self._candidate_commands():
            executable = command[0]
            if shutil.which(executable):
                available.append(executable)
        return available

    def status(self) -> Dict[str, object]:
        available_commands = self._available_commands()
        return {
            "mode": self.mode,
            "available": bool(available_commands),
            "available_commands": available_commands,
            "last_backend": self._last_backend,
            "last_error": self._last_error,
        }

    def snapshot(self) -> bytes:
        failures: List[str] = []

        for command in self._candidate_commands():
            executable = command[0]
            if shutil.which(executable) is None:
                failures.append(f"{executable}: not installed")
                continue

            try:
                logger.debug("Trying camera backend=%s", executable)
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=max(1, self.settings.timeout_ms) / 1000.0 + 2.0,
                )
            except subprocess.TimeoutExpired:
                logger.warning("Camera backend timed out backend=%s", executable)
                failures.append(f"{executable}: timed out")
                continue
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", errors="replace").strip()
                logger.warning("Camera backend failed backend=%s error=%s", executable, stderr or "command failed")
                failures.append(f"{executable}: {stderr or 'command failed'}")
                continue

            payload = completed.stdout
            if not payload or not _jpeg_looks_valid(payload):
                logger.warning("Camera backend returned invalid JPEG backend=%s size_bytes=%d", executable, len(payload))
                failures.append(f"{executable}: did not return a valid JPEG")
                continue

            self._last_backend = executable
            self._last_error = None
            logger.info("Camera snapshot succeeded backend=%s size_bytes=%d", executable, len(payload))
            return payload

        self._last_error = "; ".join(failures) if failures else "No camera backend is available."
        logger.warning("Camera snapshot unavailable: %s", self._last_error)
        raise CameraError(self._last_error)


def build_camera_service(mode: str) -> BaseCameraService:
    settings = CameraSettings.from_env()

    if mode == "mock":
        return MockCameraService(settings=settings)

    return CommandCameraService(settings=settings)
