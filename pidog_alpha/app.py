from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .catalog import LED_STYLES, build_catalog
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
    wait: bool = Field(default=True, description="Queued for ordering with other robot commands.")


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    controller = build_controller(mode=settings.mode, sound_dir=settings.sound_dir)
    service = PidogCommandService(controller)

    app.state.settings = settings
    app.state.service = service
    app.state.catalog = build_catalog(controller.sound_dir)
    yield
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
        return request.app.state.service.health()

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

    @app.post("/leds/set", dependencies=[Depends(require_api_token)])
    def set_led(payload: LedRequest, request: Request) -> Dict[str, Any]:
        _validate_led_style(payload.style)

        service = request.app.state.service
        body = _model_dump(payload)

        try:
            return service.submit(
                kind="led",
                payload=body,
                operation=lambda: service.controller.set_led(
                    style=payload.style,
                    color=payload.color,
                    bps=payload.bps,
                    brightness=payload.brightness,
                ),
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
