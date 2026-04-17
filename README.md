# PiDog Remote Control API

This repo now provides a small HTTP API for a SunFounder PiDog running Raspberry Pi OS. The service wraps the upstream `pidog` Python library and exposes a safe set of preconfigured robot actions, sound effects, and LED strip modes.

The API is designed for robot-side deployment:

- Commands are serialized through a single worker so motions, audio, and LED changes do not overlap unpredictably.
- The service supports `real`, `mock`, and `auto` startup modes.
- An optional API token can protect all control endpoints.
- The catalog endpoint publishes the supported action names, sounds, and LED styles.

## Upstream PiDog setup

Install the official SunFounder dependencies on the Pi first. Their repo and docs are here:

- `https://github.com/sunfounder/pidog`
- `https://docs.sunfounder.com/projects/pidog/en/latest/python/python_start/install_all_modules.html`

Typical robot-side setup:

```bash
sudo apt install git python3-pip python3-setuptools python3-smbus
git clone -b v2.0 https://github.com/sunfounder/robot-hat.git
cd robot-hat
sudo pip3 install . --break-system-packages

cd ~
git clone -b picamera2 https://github.com/sunfounder/vilib.git
cd vilib
sudo pip3 install . --break-system-packages

cd ~
git clone https://github.com/sunfounder/pidog.git
cd pidog
sudo pip3 install . --break-system-packages
sudo bash i2samp.sh
```

## This service

Create a venv and install this repo on the Pi:

```bash
cd ~/pidog-alpha
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install .
```

Run it with the venv's Python:

```bash
PIDOG_API_MODE=real \
PIDOG_API_HOST=0.0.0.0 \
PIDOG_API_PORT=8000 \
.venv/bin/python main.py
```

Optional environment variables:

- `PIDOG_API_MODE=auto|real|mock`
- `PIDOG_API_TOKEN=your-secret-token`
- `PIDOG_AUTH_USERNAME=admin`
- `PIDOG_AUTH_PASSWORD_HASH=pbkdf2_sha256$...`
- `PIDOG_AUTH_PASSWORD=plain-text-password`
- `PIDOG_AUTH_SECRET=long-random-signing-secret`
- `PIDOG_AUTH_TOKEN_TTL_SECONDS=43200`
- `PIDOG_AUTH_DISABLED=false`
- `PIDOG_SOUND_DIR=/home/pi/pidog/sounds`
- `PIDOG_PYTHONPATH=/custom/python/path:/another/path`
- `PIDOG_CAMERA_COMMAND="rpicam-jpeg --nopreview --timeout 3000 -o -"`
- `PIDOG_CAMERA_TIMEOUT_MS=3000`
- `PIDOG_API_CORS_ORIGINS=http://your-ui.local,http://another-host`

## Authentication

In real mode, control endpoints require either a login token or `PIDOG_API_TOKEN`. Mock mode allows unauthenticated requests unless auth is configured.

Generate a password hash:

```bash
.venv/bin/python -m pidog_alpha.auth hash-password
```

`PIDOG_AUTH_PASSWORD` is accepted for quick testing, but `PIDOG_AUTH_PASSWORD_HASH` is preferred for deployment.

Generate a signing secret:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

Start with login auth:

```bash
PIDOG_API_MODE=real \
PIDOG_AUTH_USERNAME=admin \
PIDOG_AUTH_PASSWORD_HASH='pbkdf2_sha256$...' \
PIDOG_AUTH_SECRET='replace-with-generated-secret' \
.venv/bin/python main.py
```

Login:

```bash
curl -X POST http://pidog.local:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}'
```

Use the returned `access_token` on protected requests:

```bash
curl http://pidog.local:8000/status \
  -H "Authorization: Bearer your-access-token"
```

`PIDOG_API_TOKEN` is still supported for automation. Send it as either `Authorization: Bearer <token>` or `X-API-Key: <token>`.

Swagger UI at `/docs` includes Authorize options for both `BearerAuth` and `ApiKeyAuth`.

If you want to develop off-device, use:

```bash
PIDOG_API_MODE=mock .venv/bin/python main.py
```

## Endpoints

- Public: `GET /health`, `GET /catalog`, `POST /auth/login`, `POST /auth/logout`
- Protected: `GET /auth/me`, `GET /status`, `GET /camera/snapshot`, `GET /jobs/{job_id}`
- Protected: `GET /sounds`, `POST /actions/run`, `POST /sounds/play`, `POST /sounds/upload`, `DELETE /sounds/{name}`
- Protected: `POST /leds/set`, `POST /stop`

## Example requests

Check health:

```bash
curl http://pidog.local:8000/health
```

List available commands and sounds:

```bash
curl http://pidog.local:8000/catalog
```

List playable sounds:

```bash
curl http://pidog.local:8000/sounds \
  -H "Authorization: Bearer your-secret-token"
```

Grab a camera snapshot:

```bash
curl http://pidog.local:8000/camera/snapshot \
  -H "Authorization: Bearer your-secret-token" \
  --output snapshot.jpg
```

Run a movement:

```bash
curl -X POST http://pidog.local:8000/actions/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{"name":"sit","speed":80,"step_count":1,"wait":true}'
```

Play a sound:

```bash
curl -X POST http://pidog.local:8000/sounds/play \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{"name":"single_bark_1","volume":100,"wait":false}'
```

Upload a sound:

```bash
curl -X POST http://pidog.local:8000/sounds/upload \
  -H "Authorization: Bearer your-secret-token" \
  -F "file=@single_bark_3.mp3" \
  -F "name=single_bark_3"
```

Delete a sound file:

```bash
curl -X DELETE http://pidog.local:8000/sounds/single_bark_3 \
  -H "Authorization: Bearer your-secret-token"
```

Set the LED strip:

```bash
curl -X POST http://pidog.local:8000/leds/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{"style":"listen","color":"cyan","bps":1.2,"brightness":0.8,"runtime_seconds":5,"wait":false}'
```

Omit `runtime_seconds` to leave the LED mode running until another LED command changes it. The request also accepts `time` as a shorter alias for `runtime_seconds`.

Emergency stop and lie down:

```bash
curl -X POST http://pidog.local:8000/stop \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{"lie_down":true,"speed":80}'
```

## systemd

A starter unit file is included at `deploy/pidog-web.service`.

Copy it into place on the Pi, then adjust the paths and user if needed:

```bash
sudo cp deploy/pidog-web.service /etc/systemd/system/pidog-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now pidog-web.service
sudo systemctl status pidog-web.service
```

## Notes

- `auto` mode tries the real PiDog backend first and falls back to mock mode if the hardware stack cannot be initialized.
- The service now looks for the PiDog stack in the vendored checkout at `vendor/pidog-upstream`, `~/pidog`, and common Raspberry Pi system package locations before falling back. Use `PIDOG_PYTHONPATH` if your install lives somewhere else.
- The API only exposes named actions from a built-in catalog. It does not run arbitrary Python.
- Uploaded sounds are stored in `PIDOG_SOUND_DIR` when set. Otherwise the service uses the first available sound directory from `~/pidog/sounds` or the vendored `vendor/pidog-upstream/sounds` directory.
- `DELETE /sounds/{name}` removes a matching `.mp3` or `.wav` file from the active sound directory. Built-in fallback sound names cannot be deleted unless they exist as files in that directory.
- Camera snapshots use standard Raspberry Pi still-image tools. The service tries `rpicam-jpeg`, then `libcamera-jpeg`, then `libcamera-still`, and returns a JPEG from `/camera/snapshot`.
- Sound playback works best when the process has the same permissions expected by the upstream PiDog audio tooling. On many PiDog setups that means starting the service with sufficient privileges for audio access.
- `GET /health` includes the controller `backend_source`, which is useful for confirming that the API booted against the real PiDog library instead of mock mode.
