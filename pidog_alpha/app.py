from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from time import sleep
from typing import Any, Dict, List, Optional
import os
import re

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, Security, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, root_validator

from .auth import AuthError, create_access_token, verify_access_token, verify_password
from .catalog import LED_STYLES, build_catalog, ensure_sound_dir
from .camera import CameraError, build_camera_service
from .controller import DEFAULT_IDLE_ACTIONS, ControllerError, PidogCommandService, build_controller


bearer_auth = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Use a login access token from /auth/login, or the legacy PIDOG_API_TOKEN value.",
)
x_api_key_auth = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="ApiKeyAuth",
    description="Use the legacy PIDOG_API_TOKEN value.",
)


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_bool(name: str) -> Optional[bool]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return None
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


@dataclass
class Settings:
    mode: str
    host: str
    port: int
    token: str
    sound_dir: Optional[str]
    cors_origins: List[str]
    auth_username: str
    auth_password_hash: str
    auth_password: str
    auth_secret: str
    auth_token_ttl_seconds: int
    auth_disabled: bool
    idle_enabled: Optional[bool]
    idle_actions: List[str]
    idle_min_interval_seconds: float
    idle_max_interval_seconds: float
    idle_speed: int

    @classmethod
    def from_env(cls) -> "Settings":
        raw_origins = os.getenv("PIDOG_API_CORS_ORIGINS", "*")
        raw_idle_actions = os.getenv("PIDOG_IDLE_ACTIONS", ",".join(DEFAULT_IDLE_ACTIONS))
        return cls(
            mode=os.getenv("PIDOG_API_MODE", "auto").lower(),
            host=os.getenv("PIDOG_API_HOST", "0.0.0.0"),
            port=int(os.getenv("PIDOG_API_PORT", "8000")),
            token=os.getenv("PIDOG_API_TOKEN", "").strip(),
            sound_dir=os.getenv("PIDOG_SOUND_DIR"),
            cors_origins=[origin.strip() for origin in raw_origins.split(",") if origin.strip()],
            auth_username=os.getenv("PIDOG_AUTH_USERNAME", "admin").strip(),
            auth_password_hash=os.getenv("PIDOG_AUTH_PASSWORD_HASH", "").strip(),
            auth_password=os.getenv("PIDOG_AUTH_PASSWORD", ""),
            auth_secret=os.getenv("PIDOG_AUTH_SECRET", "").strip(),
            auth_token_ttl_seconds=int(os.getenv("PIDOG_AUTH_TOKEN_TTL_SECONDS", "43200")),
            auth_disabled=_env_bool("PIDOG_AUTH_DISABLED", default=False),
            idle_enabled=_env_optional_bool("PIDOG_IDLE_ENABLED"),
            idle_actions=[action.strip() for action in raw_idle_actions.split(",") if action.strip()],
            idle_min_interval_seconds=_env_float("PIDOG_IDLE_MIN_INTERVAL_SECONDS", 8.0),
            idle_max_interval_seconds=_env_float("PIDOG_IDLE_MAX_INTERVAL_SECONDS", 18.0),
            idle_speed=int(os.getenv("PIDOG_IDLE_SPEED", "60")),
        )

    def login_configured(self) -> bool:
        return bool(
            self.auth_username
            and self.auth_secret
            and self.auth_token_ttl_seconds > 0
            and (self.auth_password_hash or self.auth_password)
        )

    def api_key_configured(self) -> bool:
        return bool(self.token)

    def any_auth_configured(self) -> bool:
        return self.login_configured() or self.api_key_configured()

    def idle_enabled_for(self, controller_mode: str) -> bool:
        if self.idle_enabled is not None:
            return self.idle_enabled
        return controller_mode == "real"


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


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


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


def _auth_required_without_configuration(request: Request) -> bool:
    settings = request.app.state.settings
    controller = getattr(getattr(request.app.state, "service", None), "controller", None)
    controller_mode = getattr(controller, "mode", settings.mode)
    return settings.mode == "real" or controller_mode == "real"


def _unauthorized(detail: str = "Missing or invalid credentials.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validate_login_credentials(settings: Settings, payload: LoginRequest) -> None:
    if payload.username != settings.auth_username:
        raise _unauthorized()

    if settings.auth_password_hash:
        if verify_password(payload.password, settings.auth_password_hash):
            return None
        raise _unauthorized()

    if settings.auth_password and compare_digest(payload.password, settings.auth_password):
        return None

    raise _unauthorized()


def require_authentication(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_auth),
    x_api_key: Optional[str] = Security(x_api_key_auth),
) -> None:
    settings = request.app.state.settings

    if settings.auth_disabled:
        return None

    if settings.token and x_api_key and x_api_key == settings.token:
        return None

    bearer_token = bearer.credentials if bearer is not None else None
    if settings.token and bearer_token and bearer_token == settings.token:
        return None

    login_token = bearer_token or request.cookies.get("pidog_access_token")
    if login_token and settings.login_configured():
        try:
            verify_access_token(
                token=login_token,
                secret=settings.auth_secret,
                expected_username=settings.auth_username,
            )
        except AuthError as exc:
            raise _unauthorized(str(exc)) from exc
        return None

    if settings.any_auth_configured():
        raise _unauthorized()

    if _auth_required_without_configuration(request):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is required in real mode. Configure PIDOG_AUTH_* credentials or PIDOG_API_TOKEN.",
        )

    return None


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


def _refresh_catalog(app: FastAPI, sound_dir: Optional[Path]) -> None:
    app.state.service.controller.sound_dir = sound_dir
    app.state.catalog = build_catalog(sound_dir)


def _sound_file_for_delete(sound_dir: Optional[Path], name: str) -> Optional[Path]:
    sound_name = _sanitize_sound_name(name)
    if sound_name != name:
        raise HTTPException(status_code=400, detail="Sound name must contain letters, numbers, '-' or '_'.")
    if sound_dir is None or not sound_dir.is_dir():
        return None

    resolved_dir = sound_dir.resolve()
    for suffix in (".mp3", ".wav"):
        candidate = (resolved_dir / f"{sound_name}{suffix}").resolve()
        if candidate.parent == resolved_dir and candidate.is_file():
            return candidate
    return None


def _sound_list_response(request: Request) -> Dict[str, Any]:
    sound_dir = request.app.state.service.controller.sound_dir
    _refresh_catalog(request.app, sound_dir)
    sounds = request.app.state.catalog["sounds"]
    return {
        "sounds": sounds,
        "sound_directory": request.app.state.catalog["sound_directory"],
        "catalog_size": len(sounds),
        "mode": request.app.state.service.controller.mode,
    }


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
    service = PidogCommandService(
        controller,
        idle_enabled=settings.idle_enabled_for(controller.mode),
        idle_actions=settings.idle_actions,
        idle_min_interval_seconds=settings.idle_min_interval_seconds,
        idle_max_interval_seconds=settings.idle_max_interval_seconds,
        idle_speed=settings.idle_speed,
    )

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

    @app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
    def login(payload: LoginRequest, response: Response, request: Request) -> LoginResponse:
        settings = request.app.state.settings
        if settings.auth_disabled:
            raise HTTPException(status_code=403, detail="Authentication is disabled.")
        if not settings.login_configured():
            raise HTTPException(status_code=503, detail="Login authentication is not configured.")

        _validate_login_credentials(settings, payload)
        token = create_access_token(
            username=settings.auth_username,
            secret=settings.auth_secret,
            ttl_seconds=settings.auth_token_ttl_seconds,
        )
        response.set_cookie(
            key="pidog_access_token",
            value=token,
            max_age=settings.auth_token_ttl_seconds,
            httponly=True,
            samesite="lax",
        )
        return LoginResponse(access_token=token, expires_in=settings.auth_token_ttl_seconds)

    @app.post("/auth/logout", tags=["auth"])
    def logout(response: Response) -> Dict[str, Any]:
        response.delete_cookie(key="pidog_access_token")
        return {"ok": True}

    @app.get("/auth/me", tags=["auth"], dependencies=[Security(require_authentication)])
    def auth_me(request: Request) -> Dict[str, Any]:
        settings = request.app.state.settings
        return {
            "authenticated": not settings.auth_disabled and settings.any_auth_configured(),
            "username": settings.auth_username if settings.login_configured() else None,
            "login_configured": settings.login_configured(),
            "api_key_configured": settings.api_key_configured(),
            "mode": request.app.state.service.controller.mode,
        }

    @app.get("/status", dependencies=[Security(require_authentication)])
    def status_snapshot(request: Request) -> Dict[str, Any]:
        return {
            "catalog_size": {
                "actions": len(request.app.state.catalog["actions"]),
                "sounds": len(request.app.state.catalog["sounds"]),
            },
            "health": request.app.state.service.health(),
            "camera": request.app.state.camera.status(),
        }

    @app.get("/jobs/{job_id}", dependencies=[Security(require_authentication)])
    def get_job(job_id: str, request: Request) -> Dict[str, Any]:
        try:
            return request.app.state.service.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown job.") from exc

    @app.post("/actions/run", dependencies=[Security(require_authentication)])
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

    @app.post("/sounds/play", dependencies=[Security(require_authentication)])
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

    @app.get("/sounds", dependencies=[Security(require_authentication)])
    def list_playable_sounds(request: Request) -> Dict[str, Any]:
        return _sound_list_response(request)

    @app.post("/sounds/upload", dependencies=[Security(require_authentication)])
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

    @app.delete("/sounds/{name}", dependencies=[Security(require_authentication)])
    def delete_sound(name: str, request: Request) -> Dict[str, Any]:
        service = request.app.state.service
        sound_file = _sound_file_for_delete(service.controller.sound_dir, name)
        if sound_file is None:
            raise HTTPException(status_code=404, detail="Sound file not found.")

        try:
            sound_file.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete sound: {exc}") from exc

        _refresh_catalog(request.app, service.controller.sound_dir)
        sounds = request.app.state.catalog["sounds"]
        return {
            "ok": True,
            "deleted": {
                "name": name,
                "filename": sound_file.name,
            },
            "sound_directory": request.app.state.catalog["sound_directory"],
            "catalog_size": len(sounds),
            "sounds": sounds,
            "mode": service.controller.mode,
        }

    @app.get(
        "/camera/snapshot",
        dependencies=[Security(require_authentication)],
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

    @app.post("/leds/set", dependencies=[Security(require_authentication)])
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

    @app.post("/stop", dependencies=[Security(require_authentication)])
    def stop_robot(payload: StopRequest, request: Request) -> Dict[str, Any]:
        return request.app.state.service.stop_now(
            lie_down=payload.lie_down,
            speed=payload.speed,
        )

    return app
