from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any, Callable, Dict, Optional
import os
import sys
import threading
import uuid

from .catalog import BASE_ACTIONS, COMPOSITE_ACTIONS, PROJECT_ROOT, resolve_sound_dir


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControllerError(RuntimeError):
    """Raised when the robot backend cannot satisfy a request."""


def _python_version_dirs() -> tuple[str, str]:
    return f"python{sys.version_info.major}.{sys.version_info.minor}", f"python{sys.version_info.major}"


def _iter_backend_search_paths() -> list[Path]:
    py_major_minor, py_major = _python_version_dirs()
    candidates: list[Path] = []

    extra_paths = os.getenv("PIDOG_PYTHONPATH", "").strip()
    if extra_paths:
        for raw_path in extra_paths.split(os.pathsep):
            if raw_path.strip():
                candidates.append(Path(raw_path).expanduser())

    candidates.extend(
        [
            PROJECT_ROOT / "vendor" / "pidog-upstream",
            Path.home() / "pidog",
            Path("/usr/local/lib") / py_major_minor / "dist-packages",
            Path("/usr/local/lib") / py_major_minor / "site-packages",
            Path("/usr/lib") / py_major_minor / "dist-packages",
            Path("/usr/lib") / py_major_minor / "site-packages",
            Path("/usr/lib") / py_major / "dist-packages",
            Path("/usr/lib") / py_major / "site-packages",
        ]
    )

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        key = str(resolved)
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        unique_candidates.append(resolved)
    return unique_candidates


def _prepare_backend_imports() -> list[str]:
    added_paths: list[str] = []
    for candidate in _iter_backend_search_paths():
        candidate_str = str(candidate)
        if candidate_str in sys.path:
            continue
        sys.path.insert(0, candidate_str)
        added_paths.append(candidate_str)
    return added_paths


def _detect_backend_source(module: Any) -> Optional[str]:
    module_path = getattr(module, "__file__", None)
    if not module_path:
        return None
    return str(Path(module_path).resolve().parent.parent)


class BaseRobotController:
    mode = "base"

    def __init__(
        self,
        sound_dir: Optional[Path],
        init_error: Optional[str] = None,
        backend_source: Optional[str] = None,
    ):
        self.sound_dir = sound_dir
        self.init_error = init_error
        self.backend_source = backend_source

    def run_action(self, name: str, speed: int = 80, step_count: int = 1) -> Dict[str, Any]:
        raise NotImplementedError

    def play_sound(self, name: str, volume: int = 100, wait: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    def set_led(
        self,
        style: str,
        color: Any = "white",
        bps: float = 1.0,
        brightness: float = 1.0,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def stop(self, lie_down: bool = False, speed: int = 80) -> Dict[str, Any]:
        raise NotImplementedError

    def snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class MockRobotController(BaseRobotController):
    mode = "mock"

    def __init__(self, sound_dir: Optional[Path], init_error: Optional[str] = None):
        super().__init__(sound_dir=sound_dir, init_error=init_error)
        self.last_action: Optional[Dict[str, Any]] = None
        self.last_sound: Optional[Dict[str, Any]] = None
        self.last_led: Dict[str, Any] = {"style": "off", "color": "black", "bps": 1.0, "brightness": 0.0}
        self.stop_count = 0

    def run_action(self, name: str, speed: int = 80, step_count: int = 1) -> Dict[str, Any]:
        if name not in BASE_ACTIONS and name not in COMPOSITE_ACTIONS:
            raise ControllerError("Unknown action.")

        sleep(min(0.05 * step_count, 0.3))
        self.last_action = {"name": name, "speed": speed, "step_count": step_count}
        return {"ok": True, "action": self.last_action, "mode": self.mode}

    def play_sound(self, name: str, volume: int = 100, wait: bool = False) -> Dict[str, Any]:
        self.last_sound = {"name": name, "volume": volume, "wait": wait}
        if wait:
            sleep(0.25)
        return {"ok": True, "sound": self.last_sound, "mode": self.mode}

    def set_led(
        self,
        style: str,
        color: Any = "white",
        bps: float = 1.0,
        brightness: float = 1.0,
    ) -> Dict[str, Any]:
        self.last_led = {
            "style": style,
            "color": color,
            "bps": bps,
            "brightness": brightness,
        }
        return {"ok": True, "led": self.last_led, "mode": self.mode}

    def stop(self, lie_down: bool = False, speed: int = 80) -> Dict[str, Any]:
        self.stop_count += 1
        if lie_down:
            self.last_action = {"name": "lie", "speed": speed, "step_count": 1}
        return {"ok": True, "stopped": True, "lie_down": lie_down, "mode": self.mode}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "sound_directory": str(self.sound_dir) if self.sound_dir else None,
            "backend_source": self.backend_source,
            "last_action": self.last_action,
            "last_sound": self.last_sound,
            "last_led": self.last_led,
            "stop_count": self.stop_count,
            "init_error": self.init_error,
        }


class RealRobotController(BaseRobotController):
    mode = "real"

    def __init__(self, sound_dir: Optional[Path]):
        added_paths = _prepare_backend_imports()

        from pidog import Pidog
        from pidog.preset_actions import (
            bark,
            bark_action,
            body_twisting,
            hand_shake,
            high_five,
            howling,
            pant,
            scratch,
        )
        import pidog as pidog_module

        backend_source = _detect_backend_source(pidog_module)
        if backend_source is None and added_paths:
            backend_source = added_paths[0]

        super().__init__(sound_dir=sound_dir, backend_source=backend_source)

        self._dog = Pidog()
        self._last_action: Optional[Dict[str, Any]] = None
        self._last_sound: Optional[Dict[str, Any]] = None
        self._last_led: Dict[str, Any] = {"style": "off", "color": "black", "bps": 1.0, "brightness": 0.0}
        self._composite_actions: Dict[str, Callable[[int, int], None]] = {
            "bark": lambda speed, step_count: self._repeat(
                step_count, lambda: bark(self._dog, volume=100)
            ),
            "bark_harder": lambda speed, step_count: self._repeat(
                step_count, lambda: bark_action(self._dog, speak="single_bark_1", volume=100)
            ),
            "pant": lambda speed, step_count: self._repeat(
                step_count, lambda: pant(self._dog, speed=max(20, speed), volume=100)
            ),
            "scratch": lambda speed, step_count: self._repeat(step_count, lambda: scratch(self._dog)),
            "handshake": lambda speed, step_count: self._repeat(step_count, lambda: hand_shake(self._dog)),
            "high_five": lambda speed, step_count: self._repeat(step_count, lambda: high_five(self._dog)),
            "howling": lambda speed, step_count: self._repeat(step_count, lambda: howling(self._dog)),
            "body_twisting": lambda speed, step_count: self._repeat(
                step_count, lambda: body_twisting(self._dog)
            ),
        }

    @staticmethod
    def _repeat(step_count: int, operation: Callable[[], None]) -> None:
        for _ in range(step_count):
            operation()

    def _resolve_sound_name(self, name: str) -> str:
        if self.sound_dir:
            for suffix in (".mp3", ".wav"):
                candidate = self.sound_dir / "{name}{suffix}".format(name=name, suffix=suffix)
                if candidate.is_file():
                    return str(candidate)
        return name

    def run_action(self, name: str, speed: int = 80, step_count: int = 1) -> Dict[str, Any]:
        if name in self._composite_actions:
            self._composite_actions[name](speed, step_count)
        elif name in BASE_ACTIONS:
            self._dog.do_action(name, step_count=step_count, speed=speed)
            self._dog.wait_all_done()
        else:
            raise ControllerError("Unknown action.")

        self._last_action = {"name": name, "speed": speed, "step_count": step_count}
        return {"ok": True, "action": self._last_action, "mode": self.mode}

    def play_sound(self, name: str, volume: int = 100, wait: bool = False) -> Dict[str, Any]:
        target = self._resolve_sound_name(name)
        if wait:
            played = self._dog.speak_block(target, volume=volume)
        else:
            played = self._dog.speak(target, volume=volume)

        if played is False:
            raise ControllerError("Unknown sound.")

        self._last_sound = {"name": name, "volume": volume, "wait": wait}
        return {"ok": True, "sound": self._last_sound, "mode": self.mode}

    def set_led(
        self,
        style: str,
        color: Any = "white",
        bps: float = 1.0,
        brightness: float = 1.0,
    ) -> Dict[str, Any]:
        if style == "off":
            self._dog.rgb_strip.close()
        else:
            self._dog.rgb_strip.set_mode(style=style, color=color, bps=bps, brightness=brightness)

        self._last_led = {
            "style": style,
            "color": color,
            "bps": bps,
            "brightness": brightness,
        }
        return {"ok": True, "led": self._last_led, "mode": self.mode}

    def stop(self, lie_down: bool = False, speed: int = 80) -> Dict[str, Any]:
        self._dog.body_stop()
        if lie_down:
            self._dog.stop_and_lie(speed=speed)
            self._last_action = {"name": "lie", "speed": speed, "step_count": 1}
        return {"ok": True, "stopped": True, "lie_down": lie_down, "mode": self.mode}

    def snapshot(self) -> Dict[str, Any]:
        battery_voltage = None
        distance_cm = None

        try:
            battery_voltage = self._dog.get_battery_voltage()
        except Exception:
            battery_voltage = None

        try:
            distance_cm = self._dog.read_distance()
        except Exception:
            distance_cm = None

        return {
            "mode": self.mode,
            "sound_directory": str(self.sound_dir) if self.sound_dir else None,
            "backend_source": self.backend_source,
            "battery_voltage": battery_voltage,
            "distance_cm": distance_cm,
            "last_action": self._last_action,
            "last_sound": self._last_sound,
            "last_led": self._last_led,
        }

    def close(self) -> None:
        self._dog.close()


def build_controller(mode: str = "auto", sound_dir: Optional[str] = None) -> BaseRobotController:
    resolved_sound_dir = resolve_sound_dir(sound_dir)

    if mode == "mock":
        return MockRobotController(sound_dir=resolved_sound_dir)

    if mode == "real":
        return RealRobotController(sound_dir=resolved_sound_dir)

    try:
        return RealRobotController(sound_dir=resolved_sound_dir)
    except Exception as exc:
        return MockRobotController(sound_dir=resolved_sound_dir, init_error=str(exc))


@dataclass
class JobRecord:
    id: str
    kind: str
    payload: Dict[str, Any]
    status: str = "submitted"
    created_at: str = field(default_factory=utc_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    future: Optional[Future] = field(default=None, repr=False)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }


class PidogCommandService:
    def __init__(self, controller: BaseRobotController):
        self.controller = controller
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pidog-api")
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def _run_job(self, job_id: str, operation: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = utc_now()

        try:
            result = operation()
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.completed_at = utc_now()
                job.error = str(exc)
            raise

        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.completed_at = utc_now()
            job.result = result
        return result

    def submit(
        self,
        kind: str,
        payload: Dict[str, Any],
        operation: Callable[[], Dict[str, Any]],
        wait: bool,
    ) -> Dict[str, Any]:
        job = JobRecord(id=str(uuid.uuid4()), kind=kind, payload=payload)
        with self._lock:
            self._jobs[job.id] = job

        job.future = self._executor.submit(self._run_job, job.id, operation)

        if wait:
            job.future.result()
        return self.get_job(job.id)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job.snapshot()

    def cancel_pending_jobs(self) -> Dict[str, Any]:
        cancelled_ids = []

        with self._lock:
            jobs = list(self._jobs.values())

        for job in jobs:
            if job.status != "submitted" or job.future is None:
                continue
            if job.future.cancel():
                with self._lock:
                    job.status = "cancelled"
                    job.completed_at = utc_now()
                cancelled_ids.append(job.id)

        return {"cancelled_job_ids": cancelled_ids}

    def stop_now(self, lie_down: bool = False, speed: int = 80) -> Dict[str, Any]:
        cancelled = self.cancel_pending_jobs()
        result = self.controller.stop(lie_down=lie_down, speed=speed)
        result.update(cancelled)
        return result

    def health(self) -> Dict[str, Any]:
        with self._lock:
            jobs = [job.snapshot() for job in self._jobs.values()]

        return {
            "ok": True,
            "controller": self.controller.snapshot(),
            "queue": {
                "total_jobs": len(jobs),
                "running_jobs": len([job for job in jobs if job["status"] == "running"]),
                "pending_jobs": len([job for job in jobs if job["status"] == "submitted"]),
            },
        }

    def close(self) -> None:
        self.cancel_pending_jobs()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.controller.close()
