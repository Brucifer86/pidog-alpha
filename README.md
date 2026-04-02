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

Run it:

```bash
PIDOG_API_MODE=real \
PIDOG_API_HOST=0.0.0.0 \
PIDOG_API_PORT=8000 \
python main.py
```

Optional environment variables:

- `PIDOG_API_MODE=auto|real|mock`
- `PIDOG_API_TOKEN=your-secret-token`
- `PIDOG_SOUND_DIR=/home/pi/pidog/sounds`
- `PIDOG_API_CORS_ORIGINS=http://your-ui.local,http://another-host`

If you want to develop off-device, use:

```bash
PIDOG_API_MODE=mock python main.py
```

## Endpoints

- `GET /health`
- `GET /catalog`
- `GET /status`
- `GET /jobs/{job_id}`
- `POST /actions/run`
- `POST /sounds/play`
- `POST /leds/set`
- `POST /stop`

When `PIDOG_API_TOKEN` is set, send either:

- `Authorization: Bearer <token>`
- `X-API-Key: <token>`

## Example requests

Check health:

```bash
curl http://pidog.local:8000/health
```

List available commands and sounds:

```bash
curl http://pidog.local:8000/catalog
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

Set the LED strip:

```bash
curl -X POST http://pidog.local:8000/leds/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{"style":"listen","color":"cyan","bps":1.2,"brightness":0.8,"wait":true}'
```

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
- The API only exposes named actions from a built-in catalog. It does not run arbitrary Python.
- Sound playback works best when the process has the same permissions expected by the upstream PiDog audio tooling. On many PiDog setups that means starting the service with sufficient privileges for audio access.
