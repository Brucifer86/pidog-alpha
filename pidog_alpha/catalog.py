from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent

BASE_ACTIONS: Dict[str, str] = {
    "stand": "Stand up into the default neutral pose.",
    "sit": "Sit in place.",
    "lie": "Lie flat on the ground.",
    "lie_with_hands_out": "Lie down with the front legs stretched forward.",
    "forward": "Walk forward one gait cycle.",
    "backward": "Walk backward one gait cycle.",
    "turn_left": "Turn left one gait cycle.",
    "turn_right": "Turn right one gait cycle.",
    "trot": "Trot forward one gait cycle.",
    "stretch": "Run the built-in stretching leg motion.",
    "push_up": "Run the built-in push-up motion.",
    "doze_off": "Perform the sleepy dozing animation.",
    "nod_lethargy": "Nod off with a sleepy head motion.",
    "shake_head": "Shake the head left and right.",
    "tilting_head_left": "Tilt the head to the left.",
    "tilting_head_right": "Tilt the head to the right.",
    "tilting_head": "Alternate between left and right head tilts.",
    "head_bark": "Raise and lower the head in a bark-like motion.",
    "wag_tail": "Wag the tail.",
    "head_up_down": "Nod the head up and down.",
    "half_sit": "Move into the half-sit transition pose.",
}

COMPOSITE_ACTIONS: Dict[str, str] = {
    "bark": "Bark once with the preset bark animation.",
    "bark_harder": "Bark more aggressively with body motion and sound.",
    "pant": "Pant with head motion and sound.",
    "scratch": "Sit and scratch.",
    "handshake": "Offer a paw and shake hands.",
    "high_five": "Raise a paw for a high five.",
    "howling": "Sit and howl with matching LED effects.",
    "body_twisting": "Do the twisting body routine.",
}

LED_STYLES: Dict[str, str] = {
    "off": "Turn the LED strip off.",
    "monochromatic": "Solid color with no animation.",
    "breath": "Fade in and out smoothly.",
    "boom": "Pulse from the center outward.",
    "bark": "Quick outward bark-style pulse.",
    "speak": "Animated speaking effect.",
    "listen": "Animated listening sweep.",
}

LED_COLORS: List[str] = [
    "white",
    "black",
    "red",
    "yellow",
    "green",
    "blue",
    "cyan",
    "magenta",
    "pink",
]

DEFAULT_SOUNDS: List[str] = [
    "angry",
    "confused_1",
    "confused_2",
    "confused_3",
    "growl_1",
    "growl_2",
    "howling",
    "pant",
    "single_bark_1",
    "single_bark_2",
    "snoring",
    "woohoo",
]


def _sound_dir_candidates(explicit: Optional[str] = None) -> List[Path]:
    candidates: List[Path] = []

    env_value = explicit or os.getenv("PIDOG_SOUND_DIR")
    if env_value:
        candidates.append(Path(env_value).expanduser())

    candidates.extend(
        [
            Path.home() / "pidog" / "sounds",
            PROJECT_ROOT / "vendor" / "pidog-upstream" / "sounds",
        ]
    )
    return candidates


def resolve_sound_dir(explicit: Optional[str] = None) -> Optional[Path]:
    for candidate in _sound_dir_candidates(explicit):
        if candidate.is_dir():
            return candidate.resolve()
    return None


def ensure_sound_dir(explicit: Optional[str] = None) -> Path:
    for candidate in _sound_dir_candidates(explicit):
        if candidate.is_dir():
            return candidate.resolve()

    target = _sound_dir_candidates(explicit)[0]
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def list_sounds(sound_dir: Optional[Path]) -> List[str]:
    if sound_dir is None or not sound_dir.is_dir():
        return list(DEFAULT_SOUNDS)

    names = set()
    for path in sound_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".mp3", ".wav"}:
            names.add(path.stem)
    return sorted(names) if names else list(DEFAULT_SOUNDS)


def build_catalog(sound_dir: Optional[Path]) -> Dict[str, object]:
    actions = {}
    for name, description in BASE_ACTIONS.items():
        actions[name] = {"kind": "base", "description": description}
    for name, description in COMPOSITE_ACTIONS.items():
        actions[name] = {"kind": "composite", "description": description}

    return {
        "actions": actions,
        "sounds": list_sounds(sound_dir),
        "led_styles": LED_STYLES,
        "led_colors": LED_COLORS,
        "sound_directory": str(sound_dir) if sound_dir else None,
    }
