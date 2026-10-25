"""Which ONNX Runtime build embeddings need on this machine (ADR 0062).

The GPU build is ``onnxruntime-gpu`` plus NVIDIA's CUDA runtime, cuBLAS and
cuDNN wheels: more than 1 GB, useful only when embeddings run on an NVIDIA
GPU that Imprint's CUDA build supports. Setup installs it only when all of
these hold, and the CPU build otherwise:

- ``model.device`` asks for the GPU (``gpu`` or ``auto``);
- the GPU lock has wheels for this platform (Windows x64, Linux x64/arm64);
- the NVIDIA driver supports the lock's CUDA major;
- the GPU ``model.gpu_device`` selects has the compute capability that CUDA
  major needs (``cuda_devices``);
- no CUDA failure is recorded for this machine and build.

AMD and Intel GPUs therefore never get a CUDA package; the local LLM uses
them through its Vulkan build instead (ADR 0049).

``python -m imprint.gpu_runtime plan [--retry]`` prints the decision as JSON
for setup; ``--retry`` first forgets a recorded failure. ``check`` runs a
CUDA session on the planned GPU after setup installed the GPU build and
records the verdict in ``data/gpu_state.json``. Planning needs only the
standard library, so setup can ask before it installs anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass

from . import cuda_devices

# Platforms whose wheels every pin of requirements/gpu.lock.txt has.
GPU_PLATFORMS = frozenset({('win32', 'amd64'), ('linux', 'x86_64'), ('linux', 'aarch64')})
# The distribution each runtime installs as the `onnxruntime` package.
RUNTIMES = {'onnxruntime': 'cpu', 'onnxruntime-gpu': 'gpu'}
# Settings the plan reads: changing one can change the runtime.
SETTINGS = frozenset({'model.device', 'model.gpu_device'})
DOWNLOAD = 'more than 1 GB'
_OS_NAMES = {'win32': 'Windows', 'linux': 'Linux', 'darwin': 'macOS'}


@dataclass(frozen=True)
class Plan:
    requested: str  # model.device
    runtime: str    # cpu | gpu
    reason: str     # why, as a sentence fragment
    device: int | None = None  # CUDA index of the GPU embeddings use
    gpu: str = ''              # its name


def decide(requested: str, gpu_setting: str, *, platform_key: tuple[str, str], cuda_major: int | None,
           cuda_version: tuple[int, int] | None, devices: list[cuda_devices.Device],
           failure=lambda device: '') -> Plan:
    """The runtime for these facts. ``failure(device)`` returns the reason
    CUDA failed before on that device, or an empty string."""
    def cpu(reason: str) -> Plan:
        return Plan(requested, 'cpu', reason)

    if requested not in ('gpu', 'auto'):  # Embeddings use CUDA for these two only.
        return cpu(f'model.device is {requested}')
    if platform_key not in GPU_PLATFORMS:
        system = _OS_NAMES.get(platform_key[0], platform_key[0])
        return cpu(f'Imprint has no CUDA build for {system} on {platform_key[1]}')
    if cuda_major is None:
        return cpu("the GPU runtime's requirements are missing from this installation")
    if cuda_version is None or not devices:
        return cpu('no NVIDIA GPU found')
    if cuda_version[0] < cuda_major:
        return cpu(f'the NVIDIA driver supports CUDA {cuda_version[0]}.{cuda_version[1]} and the GPU runtime '
                   f'needs CUDA {cuda_major}: update the driver')
    minimum = cuda_devices.minimum_compute(cuda_major)
    needs = f'CUDA {cuda_major} needs compute capability {minimum[0]}.{minimum[1]} or newer' if minimum else ''

    def has(device: cuda_devices.Device) -> str:
        return f'{device.name} has {device.compute[0]}.{device.compute[1]}'

    if gpu_setting == 'auto':
        device = next((d for d in devices if cuda_devices.supported(d, cuda_major) is not False), None)
        if device is None:
            return cpu(needs + '; ' + ', '.join(has(d) for d in devices))
    else:
        device = next((d for d in devices if d.index == int(gpu_setting)), None)
        if device is None:
            return cpu(f'model.gpu_device is {gpu_setting} and there is no CUDA device {gpu_setting}')
        if cuda_devices.supported(device, cuda_major) is False:
            return cpu(f'{needs}; model.gpu_device {gpu_setting}, {has(device)}')
    failed = failure(device)
    if failed:
        return cpu(f'CUDA failed on this machine before ({failed}); imprint setup --retry-gpu tries again')
    return Plan(requested, 'gpu', f'{device.name}, CUDA {cuda_major}', device.index, device.name)


def platform_key() -> tuple[str, str]:
    return sys.platform, platform.machine().lower()


def plan(requested: str | None = None, gpu_setting: str | None = None) -> Plan:
    """The runtime this machine needs for ``model.device`` (or ``requested``)."""
    from .config_schema import resolve
    requested = str(requested or resolve('model.device')[0]).strip().lower()
    gpu_setting = str(gpu_setting or resolve('model.gpu_device')[0])
    if requested not in ('gpu', 'auto'):  # No hardware probing for the default.
        return decide(requested, gpu_setting, platform_key=platform_key(), cuda_major=None, cuda_version=None, devices=[])
    return decide(requested, gpu_setting, platform_key=platform_key(), cuda_major=cuda_devices.runtime_major(),
                  cuda_version=cuda_devices.cuda_version(), devices=cuda_devices.devices(),
                  failure=lambda device: recorded_failure(environment(device.name)))


def installed() -> str:
    """The runtime installed now: cpu, gpu, or empty when there is no single
    intact ONNX Runtime (setup repairs that)."""
    from . import onnx_runtime
    providers = onnx_runtime.providers()
    if len(providers) != 1 or not all(providers.values()):
        return ''
    return RUNTIMES.get(next(iter(providers)), '')


def pending(current: Plan | None = None) -> str:
    """What applying the plan installs, or empty when the runtime already matches."""
    current = current or plan()
    if installed() == current.runtime:
        return ''
    if current.runtime == 'gpu':
        return f'the CUDA runtime ({DOWNLOAD}) for {current.gpu}'
    return 'the CPU runtime, removing the CUDA libraries' if installed() == 'gpu' else 'the CPU runtime'


# ── Recorded CUDA verdict (data/gpu_state.json) ───────────────
# A failed check is remembered for this machine and GPU build, so setup does
# not download the GPU runtime again only to fail. Any change of GPU, driver,
# Python or GPU lock retries once; `imprint setup --retry-gpu` forces it.

def _state_path():
    from .config import get_data_dir
    return get_data_dir() / 'gpu_state.json'


def _gpu_requirements():
    locked = cuda_devices.REQUIREMENTS / 'gpu.lock.txt'
    return locked if locked.is_file() else cuda_devices.REQUIREMENTS / 'gpu.txt'


def environment(gpu: str) -> dict:
    """The fingerprint a verdict is valid for."""
    try:
        runtime = hashlib.sha256(_gpu_requirements().read_bytes()).hexdigest()[:16]
    except OSError:
        runtime = ''
    return {'gpu': gpu, 'driver': cuda_devices.driver_version(),
            'python': '%d.%d' % sys.version_info[:2], 'runtime': runtime}


def same_environment(recorded: dict, current: dict) -> bool:
    """Empty fields match anything, except the GPU build, which must be equal:
    a verdict from another build, or from before builds were recorded, says
    nothing about this one."""
    for field in ('gpu', 'driver', 'python'):
        if recorded.get(field) and current.get(field) and recorded[field] != current[field]:
            return False
    return recorded.get('runtime', '') == current.get('runtime', '')


def _load_state() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def recorded_failure(current: dict) -> str:
    state = _load_state()
    if state.get('ort_gpu') != 'broken' or not same_environment(state.get('ort_gpu_env') or {}, current):
        return ''
    return state.get('ort_gpu_reason') or 'reason not recorded'


def forget_failure() -> None:
    try:
        _state_path().unlink()
    except FileNotFoundError:
        pass


# ── CUDA check ────────────────────────────────────────────────

def _smoke(device: int) -> str:
    """Run a model on CUDA device ``device``; empty on success, else why not.

    Conv runs on cuDNN and MatMul on cuBLAS, the libraries the embedding
    models need: a session can start without cuDNN and fail only when a node
    needs it. The model declares IR version 8 because helper.make_model
    otherwise stamps the onnx package's newest one, which can exceed what
    the runtime accepts."""
    import contextlib
    import io

    import numpy as np
    import onnxruntime as ort
    from onnx import TensorProto, helper

    preload = io.StringIO()
    with contextlib.redirect_stdout(preload):
        ort.preload_dlls()
    graph = helper.make_graph(
        [helper.make_node('Conv', ['x', 'w'], ['c']),
         helper.make_node('Reshape', ['c', 'shape'], ['r']),
         helper.make_node('MatMul', ['r', 'm'], ['y'])],
        'smoke',
        [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 1, 4, 4])],
        [helper.make_tensor_value_info('y', TensorProto.FLOAT, [4, 4])],
        [helper.make_tensor('w', TensorProto.FLOAT, [1, 1, 1, 1], [2.0]),
         helper.make_tensor('shape', TensorProto.INT64, [2], [4, 4]),
         helper.make_tensor('m', TensorProto.FLOAT, [4, 4], np.eye(4, dtype=np.float32).flatten().tolist())])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)], ir_version=8)
    options = ort.SessionOptions()
    options.log_severity_level = 4
    session = ort.InferenceSession(model.SerializeToString(), sess_options=options,
                                   providers=[('CUDAExecutionProvider', {'device_id': device})])
    if 'CUDAExecutionProvider' not in session.get_providers():
        failed = [line for line in preload.getvalue().splitlines() if line.startswith('Failed to load')]
        return '; '.join(failed) or 'CUDAExecutionProvider unavailable'
    x = np.arange(16, dtype=np.float32).reshape(1, 1, 4, 4)
    y = session.run(None, {'x': x})[0]
    return '' if np.allclose(y, 2 * x.reshape(4, 4)) else 'CUDA returned a wrong result'


def check() -> tuple[bool, str]:
    """Check CUDA on the planned GPU with the installed GPU runtime and record
    the verdict. Never changes packages."""
    current = plan()
    if current.runtime != 'gpu':
        return False, current.reason
    try:
        reason = _smoke(current.device)
    except Exception as error:  # Any failure to load or run is the verdict.
        reason = (str(error).strip().splitlines() or [type(error).__name__])[0]
    state = {'ort_gpu': 'broken' if reason else 'ok', 'ort_gpu_env': environment(current.gpu)}
    if reason:
        state['ort_gpu_reason'] = reason
    _save_state(state)
    return not reason, reason or current.gpu


def main(argv: list[str]) -> int:
    command = argv[0] if argv else 'plan'
    if command == 'plan':
        if '--retry' in argv:
            forget_failure()
        print(json.dumps(asdict(plan())))
        return 0
    if command == 'check':
        ok, detail = check()
        print(json.dumps({'ok': ok, 'detail': detail}))
        return 0 if ok else 1
    print('usage: python -m imprint.gpu_runtime plan [--retry] | check', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
