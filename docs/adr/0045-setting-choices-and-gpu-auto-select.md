---
title: ADR 0045 — Settings declare their choices; the GPU device defaults to auto
description: The config schema carries known values per setting, read from the device where needed, and model.gpu_device=auto picks the first GPU the installed CUDA runtime supports.
---

- Status: Accepted; setup's CUDA check on device 0 superseded by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)
- Date: 2026-10-24
- Related: ADR 0038

## Context

On a Windows machine with an RTX 4080 (compute capability 8.9) and a GTX 1060 (6.1), `model.gpu_device=1` made every embedding call fail. ONNX Runtime raised `CUBLAS failure 8: the function requires an architectural feature absent from the device` while creating the session, so memory search failed. The GPU runtime installs CUDA 13 (ADR 0038), and CUDA 13's libraries no longer ship code for Maxwell, Pascal or Volta. The CPU provider listed after CUDA does not help, because the error is raised while the session is created, before any fallback.

The default was `0`, which is right only when device 0 happens to be supported. The dashboard showed every setting as free text, so choosing a GPU meant knowing CUDA's device numbering, and an invalid value was answered with HTTP 200 and an `error` field, which the dashboard reported as saved.

## Decision

A `Setting` in `imprint/config_schema.py` can declare `choices`: a tuple of `Choice(value, label, detail, unavailable)`, or a function that reads them from this device. `custom` says whether other values are valid too. `set_value` rejects a value outside a closed set, for the CLI and the dashboard alike. `/api/config` returns each setting's choices. The dashboard renders a dropdown for any setting with choices, plus a custom field when `custom` is set, and needs no per-setting code.

`model.gpu_device` takes `auto` or a device index, parsed by `device_index`; `auto` is the default. `imprint/cuda_devices.py` asks the CUDA driver API (`libcuda.so.1`, `nvcuda.dll`) for each device's name, memory and compute capability. These indices match ONNX Runtime's `device_id`, because both follow the driver's enumeration order. The CUDA major comes from the installed `nvidia-cuda-runtime` distribution, and `MIN_COMPUTE` maps it to the oldest supported compute capability. `auto` picks the first device not known to be below that minimum. With no such device, embeddings run on CPU and say so on stderr. The device choices mark an unsupported GPU as unavailable and say why. An explicit index is used as given.

`PUT /api/config/{key}` answers 400 for an invalid value and 404 for an unknown key.

## Alternatives

- **`nvidia-smi` for enumeration.** Its indices follow PCI bus order. CUDA's default order is fastest first, so the two can disagree and the dashboard would name the wrong card.
- **Probing each device by creating a cuBLAS handle.** This is exact, but it loads the CUDA libraries and creates a context with VRAM on every card each time settings are listed.
- **Keeping the index an integer with a sentinel such as -1 for auto.** That is less readable on the CLI (`imprint config set model.gpu_device auto`), and other auto settings already use the string `auto`.
- **Hard-coding dropdown options in the dashboard.** Options would drift from what the server accepts, and the CLI would not validate them.

## Consequences

- Every device gets a working default without configuration.
- A stored integer index still resolves: `device_index` turns `1` into `"1"`.
- `MIN_COMPUTE` must gain an entry when the runtime moves to a new CUDA major. Until then, an unknown major marks no device unsupported, which is the old behaviour.
- Setup's CUDA smoke test still runs on device 0. On a machine where device 0 is unsupported and another device is supported, setup would install the CPU runtime.
- llama.cpp offload (`*.n_gpu_layers`) is unaffected and still uses llama.cpp's own device selection.

## Validation

- `tests/test_gpu_device.py`: auto skips an unsupported device, trusts devices when the CUDA major is unknown, and returns none when nothing is supported. Embeddings use the resolved `device_id`, or CPU when there is no device. `device_index` parsing, closed-choice rejection, and the API's choices and 400/404 responses are also covered.
- `imprint/ui/tests/settings.spec.ts`: the unsupported GPU's warning is shown, a listed GPU is picked, and a custom index is entered.
- The driver enumeration was checked under WSL on the machine above. It listed device 0 as the RTX 4080 (8.9) and device 1 as the GTX 1060 (6.1).
- Not yet verified: the Windows runtime picking device 0 under `auto` after an update.
