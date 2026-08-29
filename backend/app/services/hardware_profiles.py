"""Hardware-profile defaults shared by local setup and llama-server startup."""
from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass
from typing import Literal

ProfileName = Literal["auto", "cpu", "vulkan", "cuda", "custom"]
VALID_PROFILES = {"auto", "cpu", "vulkan", "cuda", "custom"}


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    llama_variant: str
    whisper_variant: str
    gpu_layers: int


def detect_profile(requested: str | None = None) -> HardwareProfile:
    """Resolve an explicit profile or choose the best local Windows backend."""
    name = (requested or os.getenv("PARCHEE_HARDWARE_PROFILE", "auto")).lower().strip()
    if name not in VALID_PROFILES:
        raise ValueError(f"Unsupported PARCHEE_HARDWARE_PROFILE={name!r}; choose {sorted(VALID_PROFILES)}")
    if name == "auto":
        if shutil.which("nvidia-smi"):
            name = "cuda"
        elif _has_vulkan_loader():
            name = "vulkan"
        else:
            name = "cpu"
    defaults = {
        "cpu": HardwareProfile("cpu", "cpu", "cpu", 0),
        "vulkan": HardwareProfile("vulkan", "vulkan", "cpu", 999),
        "cuda": HardwareProfile("cuda", "cuda-12.4", "cuda", 999),
        "custom": HardwareProfile("custom", "custom", "custom", 0),
    }
    return defaults[name]


def _has_vulkan_loader() -> bool:
    if os.name != "nt":
        return shutil.which("vulkaninfo") is not None
    try:
        ctypes.WinDLL("vulkan-1.dll")
        return True
    except OSError:
        return False
