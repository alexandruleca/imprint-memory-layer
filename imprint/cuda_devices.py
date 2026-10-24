"""CUDA devices as the driver enumerates them, and which of them the
installed GPU runtime can run.

ONNX Runtime's ``device_id`` is a CUDA device index, which follows the
driver's enumeration order (CUDA_DEVICE_ORDER, CUDA_VISIBLE_DEVICES). The
driver API gives the same indices plus each device's compute capability,
without loading the CUDA libraries or creating a context.

Each CUDA major ships library code only from a minimum compute capability
up: CUDA 13 dropped Maxwell, Pascal and Volta. ONNX Runtime fails to start a
session on an older device (cuBLAS reports an architecture mismatch), so
``model.gpu_device=auto`` skips it.
"""
from __future__ import annotations

import ctypes
import importlib.metadata as metadata
import sys
from dataclasses import dataclass

# Oldest compute capability each CUDA major's libraries support.
MIN_COMPUTE = {11: (3, 5), 12: (5, 0), 13: (7, 5)}

_COMPUTE_MAJOR, _COMPUTE_MINOR = 75, 76  # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_*


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    compute: tuple[int, int]
    memory_mb: int


def devices() -> list[Device]:
    """CUDA devices by index; empty without an NVIDIA driver."""
    try:
        lib = ctypes.CDLL('nvcuda.dll' if sys.platform == 'win32' else 'libcuda.so.1')
    except OSError:
        return []
    count = ctypes.c_int()
    if lib.cuInit(0) or lib.cuDeviceGetCount(ctypes.byref(count)):
        return []
    found = []
    for index in range(count.value):
        device, major, minor, memory = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_size_t()
        name = ctypes.create_string_buffer(256)
        if (lib.cuDeviceGet(ctypes.byref(device), index)
                or lib.cuDeviceGetName(name, len(name), device)
                or lib.cuDeviceGetAttribute(ctypes.byref(major), _COMPUTE_MAJOR, device)
                or lib.cuDeviceGetAttribute(ctypes.byref(minor), _COMPUTE_MINOR, device)
                or lib.cuDeviceTotalMem_v2(ctypes.byref(memory), device)):
            continue
        found.append(Device(index, name.value.decode(errors='replace'), (major.value, minor.value), memory.value >> 20))
    return found


def runtime_major() -> int | None:
    """CUDA major of the runtime the GPU build installs (ADR 0038); None without it."""
    try:
        return int(metadata.version('nvidia-cuda-runtime').split('.')[0])
    except (metadata.PackageNotFoundError, ValueError):
        return None


def minimum_compute(major: int | None) -> tuple[int, int] | None:
    return MIN_COMPUTE.get(major) if major is not None else None


def supported(device: Device, major: int | None) -> bool | None:
    """Whether the runtime has code for the device; None when unknown."""
    minimum = minimum_compute(major)
    return None if minimum is None else device.compute >= minimum


def resolve(setting: str) -> int | None:
    """The device index for ``model.gpu_device``: ``auto`` is the first device
    the runtime is not known to lack code for, None when there is none."""
    if setting != 'auto':
        return int(setting)
    major = runtime_major()
    return next((d.index for d in devices() if supported(d, major) is not False), None)
