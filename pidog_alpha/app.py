from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any, Dict, List, Optional
import os
import re

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, root_validator

from .catalog import LED_STYLES, build_catalog, ensure_sound_dir
from .camera import CameraError, build_camera_service
from .controller import ControllerError, PidogCommandService, build_controller


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


@dataclass
class Settings:
    mode: str
    host: str
    port: int
    token: str
    sound_dir: Optional[str]
    cors_origins: List[str]

    @classmethod
    def from_env(cls) -> "Settings":
        raw_origins = os.getenv("PIDOG_API_CORS_ORIGINS", "*")
        return cls(
            mode=os.getenv("PIDOG_API_MODE", "auto").lower(),
            host=os.getenv("PIDOG_API_HOST", "0.0.0.0"),
            port=int(os.getenv("PIDOG_API_PORT", "8000")),
            token=os.getenv("PIDOG_API_TOKEN", "").strip(),
            sound_dir=os.getenv("PIDOG_SOUND_DIR"),
            cors_origins=[origin.strip() for origin in raw_origins.split(",") if origin.strip()],
        )


def get_settings() -> Settings:
    return Settings.from_env()


class ActionRequest(BaseModel):
    name: str = Field(description="Catalog action name to execute.")
    speed: int = Field(default=80, ge=1, le=100)
    step_count: int = Field(default=1, ge=1, le=10)
    wait: bool = Field(default=True, description="Block until the command finishes.")


class SoundRequest(BaseModel):
    name: str
    volume: int = Field(default=100, ge=0, le=100)
    wait: bool = Field(default=False, description="Use blocking playback if true.")


class LedRequest(BaseModel):
    style: str = Field(default="breath")
    color: Any = Field(default="cyan")
    bps: float = Field(default=1.0, gt=0.0, le=10.0)
    brightness: float = Field(default=1.0, gt=0.0, le=1.0)
    runtime_seconds: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Optional LED runtime in seconds before the strip is turned off.",
    )
    wait: bool = Field(default=True, description="Queued for ordering with other robot commands.")

    @root_validator(pre=True)
    def accept_time_alias(cls, values: Any) -> Any:
        if isinstance(values, dict) and "runtime_seconds" not in values and "time" in values:
            values = dict(values)
            values["runtime_seconds"] = values["time"]
        return values


class StopRequest(BaseModel):
    lie_down: bool = False
    speed: int = Field(default=80, ge=1, le=100)


def require_api_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> None:
    expected = request.app.state.settings.token
    if not expected:
        return None

    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    if x_api_key == expected or bearer_token == expected:
        return None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validate_action(app: FastAPI, name: str) -> None:
    if name not in app.state.catalog["actions"]:
        raise HTTPException(status_code=404, detail="Unknown action.")


def _validate_sound(app: FastAPI, name: str) -> None:
    if name not in app.state.catalog["sounds"]:
        raise HTTPException(status_code=404, detail="Unknown sound.")


def _validate_led_style(style: str) -> None:
    if style not in LED_STYLES:
        raise HTTPException(status_code=404, detail="Unknown LED style.")


def _sanitize_sound_name(raw_name: str) -> str:
    normalized = re.sub(r"\s+", "_", raw_name.strip())
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "", normalized)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Sound name must contain letters, numbers, '-' or '_'.")
    return safe_name


async def _save_upload_file(upload: UploadFile, destination: Path) -> int:
    size = 0
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            size += len(chunk)
    await upload.close()
    return size


def _refresh_catalog(app: FastAPI, sound_dir: Path) -> None:
    app.state.service.controller.sound_dir = sound_dir
    app.state.catalog = build_catalog(sound_dir)


def _set_led_with_runtime(service: PidogCommandService, payload: LedRequest) -> Dict[str, Any]:
    result = service.controller.set_led(
        style=payload.style,
        color=payload.color,
        bps=payload.bps,
        brightness=payload.brightness,
    )

    if payload.runtime_seconds is None:
        return result

    sleep(payload.runtime_seconds)
    off_result = service.controller.set_led(
        style="off",
        color="black",
        bps=1.0,
        brightness=0.0,
    )

    return {
        "ok": bool(result.get("ok")) and bool(off_result.get("ok")),
        "mode": off_result.get("mode", result.get("mode")),
        "led": off_result.get("led"),
        "initial_led": result.get("led"),
        "runtime_seconds": payload.runtime_seconds,
        "turned_off": True,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    controller = build_controller(mode=settings.mode, sound_dir=settings.sound_dir)
    camera = build_camera_service(mode=settings.mode)
    service = PidogCommandService(controller)

    app.state.settings = settings
    app.state.service = service
    app.state.camera = camera
    app.state.catalog = build_catalog(controller.sound_dir)
    yield
    camera.close()
    service.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PiDog Remote Control API",
        version="0.1.0",
        description="HTTP API for running safe, preconfigured PiDog motions, sounds, and LED effects.",
        lifespan=lifespan,
    )

    settings = get_settings()
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if "*" in settings.cors_origins else settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def root() -> Dict[str, Any]:
        return {
            "name": "PiDog Remote Control API",
            "docs": "/docs",
            "health": "/health",
            "catalog": "/catalog",
        }

    @app.get("/health")
    def health(request: Request) -> Dict[str, Any]:
        health_data = request.app.state.service.health()
        health_data["camera"] = request.app.state.camera.status()
        return health_data

    @app.get("/catalog")
    def catalog(request: Request) -> Dict[str, Any]:
        return request.app.state.catalog

    @app.get("/status", dependencies=[Depends(require_api_token)])
    def status_snapshot(request: Request) -> Dict[str, Any]:
        return {
            "catalog_size": {
                "actions": len(request.app.state.catalog["actions"]),
                "sounds": len(request.app.state.catalog["sounds"]),
            },
            "health": request.app.state.service.health(),
            "camera": request.app.state.camera.status(),
        }

    @app.get("/jobs/{job_id}", dependencies=[Depends(require_api_token)])
    def get_job(job_id: str, request: Request) -> Dict[str, Any]:
        try:
            return request.app.state.service.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown job.") from exc

    @app.post("/actions/run", dependencies=[Depends(require_api_token)])
    def run_action(payload: ActionRequest, request: Request) -> Dict[str, Any]:
        _validate_action(request.app, payload.name)

        service = request.app.state.service
        body = _model_dump(payload)

        try:
            return service.submit(
                kind="action",
                payload=body,
                operation=lambda: service.controller.run_action(
                    name=payload.name,
                    speed=payload.speed,
                    step_count=payload.step_count,
                ),
                wait=payload.wait,
            )
        except ControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sounds/play", dependencies=[Depends(require_api_token)])
    def play_sound(payload: SoundRequest, request: Request) -> Dict[str, Any]:
        _validate_sound(request.app, payload.name)

        service = request.app.state.service
        body = _model_dump(payload)

        try:
            return service.submit(
                kind="sound",
                payload=body,
                operation=lambda: service.controller.play_sound(
                    name=payload.name,
                    volume=payload.volume,
                    wait=payload.wait,
                ),
                wait=payload.wait,
            )
        except ControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sounds/upload", dependencies=[Depends(require_api_token)])
    async def upload_sound(
        request: Request,
        file: UploadFile = File(description="MP3 file to add to the sound catalog."),
        name: Optional[str] = Form(default=None, description="Optional catalog name. Defaults to the uploaded filename."),
        overwrite: bool = Form(default=False, description="Replace an existing sound with the same name."),
    ) -> Dict[str, Any]:
        original_name = file.filename or ""
        source_path = Path(original_name)
        extension = source_path.suffix.lower()
        if extension != ".mp3":
            raise HTTPException(status_code=400, detail="Only .mp3 uploads are supported.")

        sound_name = _sanitize_sound_name(name or source_path.stem)
        sound_dir = ensure_sound_dir(request.app.state.settings.sound_dir)
        destination = sound_dir / f"{sound_name}.mp3"

        if destination.exists() and not overwrite:
            raise HTTPException(status_code=409, detail="Sound already exists. Set overwrite=true to replace it.")

        try:
            size = await _save_upload_file(file, destination)
        except Exception as exc:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to store uploaded sound: {exc}") from exc

        _refresh_catalog(request.app, sound_dir)
        return {
            "ok": True,
            "sound": {
                "name": sound_name,
                "filename": destination.name,
                "size_bytes": size,
            },
            "sound_directory": str(sound_dir),
            "catalog_size": len(request.app.state.catalog["sounds"]),
            "mode": request.app.state.service.controller.mode,
        }

    @app.get(
        "/camera/snapshot",
        dependencies=[Depends(require_api_token)],
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    def camera_snapshot(request: Request) -> Response:
        try:
            payload = request.app.state.camera.snapshot()
        except CameraError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        status_data = request.app.state.camera.status()
        backend = status_data.get("last_backend")
        headers = {"Cache-Control": "no-store"}
        if backend:
            headers["X-Camera-Backend"] = str(backend)
        return Response(content=payload, media_type="image/jpeg", headers=headers)

    @app.post("/leds/set", dependencies=[Depends(require_api_token)])
    def set_led(payload: LedRequest, request: Request) -> Dict[str, Any]:
        _validate_led_style(payload.style)

        service = request.app.state.service
        body = _model_dump(payload)

        try:
            return service.submit(
                kind="led",
                payload=body,
                operation=lambda: _set_led_with_runtime(service, payload),
                wait=payload.wait,
            )
        except ControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/stop", dependencies=[Depends(require_api_token)])
    def stop_robot(payload: StopRequest, request: Request) -> Dict[str, Any]:
        return request.app.state.service.stop_now(
            lie_down=payload.lie_down,
            speed=payload.speed,
        )

    return app
