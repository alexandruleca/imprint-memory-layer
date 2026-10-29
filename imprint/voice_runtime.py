"""Explicit, isolated CPU build for the live LFM voice pilot (ADR 0043)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import lfm_audio as lfm
from .personaplex import PrototypeError
from .downloads import expected_digest

SOURCE_REVISION = "ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b"
SOURCE_SHA256 = expected_digest("lfm-audio-live-source", SOURCE_REVISION, "live-source.tar.gz")
PATCH_VERSION = 2
SERVER = "llama-liquid-audio-server"
BUILD_VERSION = 3


def target() -> str:
    system, machine = platform.system(), platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if (system, arch) not in {("Linux", "x64"), ("Windows", "x64"), ("Darwin", "arm64")}:
        raise PrototypeError("Voice supports Linux x64, Windows x64, and macOS on Apple silicon.")
    return {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}[system] + "-" + arch


def supported() -> bool:
    try:
        target()
        return True
    except PrototypeError:
        return False


def native_binary(root: Path, expected_target: str | None = None) -> Path:
    current = expected_target or target()
    if current not in {"linux-x64", "windows-x64", "macos-arm64"}:
        raise PrototypeError("Unsupported voice engine target")
    return root / "live-native" / current / (SERVER + (".exe" if current == "windows-x64" else ""))


def child_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def child_environment() -> dict[str, str]:
    allowed = {"path", "home", "lang", "lc_all", "tmpdir", "systemroot", "windir", "temp", "tmp", "userprofile"}
    return {key: value for key, value in os.environ.items() if key.lower() in allowed}


def patched_server(source: str) -> str:
    """Fail closed if the pinned upstream source contract changes."""
    def replace(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise PrototypeError("Live voice source patch does not match the pinned revision.")
        source = source.replace(old, new)

    replace('#include "mtmd.h"', '#include "mtmd.h"\n#include <iostream>\n#include <cstdlib>')

    replace("    common_init();", '''    const char * secret = std::getenv("IMPRINT_VOICE_BACKEND_TOKEN");
    if (!secret || std::string(secret).size() < 32) { return 1; }
    const std::string authorization = "Bearer " + std::string(secret);
    // Only the owning API holds the write end. EOF also covers abrupt API exit
    // on Windows, where TerminateProcess does not run Python cleanup handlers.
    const char * lifetime = std::getenv("IMPRINT_VOICE_LIFETIME_PIPE");
    if (!lifetime || std::string(lifetime) != "1") { return 1; }
    std::thread([] { std::cin.get(); std::_Exit(0); }).detach();
    params.hostname = "127.0.0.1";
    common_init();''')
    start = source.index("    // CORS\n")
    end = source.index("    // Chat completions endpoint", start)
    source = source[:start] + '''    // Private headless worker: no browser access, no wildcard CORS.
    svr.set_payload_max_length(5 * 1024 * 1024);
    svr.set_pre_routing_handler([&authorization](const httplib::Request & req, httplib::Response & res) {
        const auto supplied = req.get_header_value("Authorization");
        unsigned difference = supplied.size() ^ authorization.size();
        for (size_t i = 0; i < authorization.size(); ++i) {
            difference |= authorization[i] ^ (i < supplied.size() ? supplied[i] : 0);
        }
        if (req.remote_addr != "127.0.0.1" || req.has_header("Origin") || difference) {
            res.status = 403;
            res.set_content("Forbidden", "text/plain");
            return httplib::Server::HandlerResponse::Handled;
        }
        return httplib::Server::HandlerResponse::Unhandled;
    });
    svr.Get("/health", [](const httplib::Request &, httplib::Response & res) {
        res.set_content("{\\"ready\\":true}", "application/json");
    });

''' + source[end:]
    replace('body.value("max_tokens", 2048)', 'std::max(16, std::min(512, body.value("max_tokens", 256)))')
    start = source.index("            auto check_abort = ")
    end = source.index("            work_queue.push", start)
    source = source[:start] + '''            // Do not retain a reference to httplib's request after disconnect.
            auto check_abort = [output, &runner, &is_server_running]() {
                if (output->aborted.load() || !is_server_running.load()) { runner.stop(); }
            };

''' + source[end:]
    replace('"text/event-stream", [output, &is_server_running]',
            '"text/event-stream", [output, &runner, &is_server_running]')
    replace("                            output->aborted = true;", "                            output->aborted = true;\n                            runner.stop();")
    replace("                    return true;\n                });", '''                    return true;
                }, [output, &runner](bool) {
                    if (!output->done.load()) { output->aborted = true; runner.stop(); }
                });''')
    return source


def install(root: Path, *, cmake: str = "cmake", jobs: int = 4, progress=None, download_prefix="") -> None:
    if not 1 <= jobs <= 16:
        raise PrototypeError("Build jobs must be 1–16.")
    if not shutil.which(cmake):
        raise PrototypeError("Install CMake and a C++ compiler, or pass --cmake /path/to/cmake.")
    lfm.install_weights(root, progress=progress, download_prefix=download_prefix)
    build_engine(root, cmake=cmake, jobs=jobs, progress=progress, download_prefix=download_prefix)


def build_engine(root: Path, *, cmake: str = "cmake", jobs: int = 4, progress=None, download_prefix="") -> None:
    """Native release builder. Engine compilation never downloads model weights."""
    current = target()
    if not 1 <= jobs <= 16:
        raise PrototypeError("Build jobs must be 1–16.")
    executable = shutil.which(cmake)
    if not executable:
        raise PrototypeError("Install CMake and a C++ compiler, or pass --cmake /path/to/cmake.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    def report(stage):
        if progress:
            progress({"stage": stage})
    from filelock import FileLock
    with FileLock(str(root / ".live-build.lock")):
        try:
            verify_engine(root)
        except PrototypeError:
            pass
        else:
            print("Verified cached live voice worker. No rebuild needed.", flush=True)
            return
        report("engine")
        archive = root / "live-source.tar.gz"
        if not archive.is_file() or lfm._digest(archive) != SOURCE_SHA256:
            url = f"https://codeload.github.com/tdakhran/llama.cpp/tar.gz/{SOURCE_REVISION}"
            fd, name = tempfile.mkstemp(dir=root, prefix=download_prefix, suffix=".part")
            try:
                with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=120) as response:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 64 * 1024 * 1024:
                            raise PrototypeError("Source archive exceeds its size budget.")
                        out.write(chunk)
                if lfm._digest(Path(name)) != SOURCE_SHA256:
                    raise PrototypeError("Live voice source checksum does not match.")
                os.replace(name, archive)
            finally:
                Path(name).unlink(missing_ok=True)
        report("preparing")
        source_root = root / "live-source"
        source_root.mkdir(mode=0o700, exist_ok=True)
        with tarfile.open(archive) as source:
            source.extractall(source_root, filter="data")
        tree = source_root / f"llama.cpp-{SOURCE_REVISION}"
        server = tree / "tools/liquid-audio/server.cpp"
        server.write_bytes(patched_server(server.read_text(encoding="utf-8")).encode("utf-8"))
        build = root / f"live-build-{current}"
        options = ["-DGGML_METAL=OFF", "-DGGML_OPENMP=OFF", "-DGGML_BLAS=OFF"]
        if current == "linux-x64":
            options += ["-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc"]
        if current == "windows-x64":
            options += ["-A", "x64", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"]
        elif current == "macos-arm64":
            options += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0", "-DCMAKE_OSX_ARCHITECTURES=arm64",
                        "-DGGML_CPU_ARM_ARCH=armv8.2-a+fp16+dotprod"]
        report("building")
        # Browser progress contains stages, never raw compiler output or paths.
        output = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if progress else {}
        subprocess.run([executable, "-S", str(tree), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                        "-DGGML_NATIVE=OFF", "-DGGML_CUDA=OFF", "-DGGML_VULKAN=OFF",
                        "-DBUILD_SHARED_LIBS=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_OPENSSL=OFF",
                        "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", *options], check=True, **output)
        subprocess.run([executable, "--build", str(build), "--config", "Release", "--target", SERVER,
                        "-j", str(jobs)], check=True, **output)
        report("finishing")
        name = SERVER + (".exe" if current == "windows-x64" else "")
        built = build / "bin" / ("Release" if current == "windows-x64" else "") / name
        binary = native_binary(root)
        binary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(built, binary)
        metadata = {"source_revision": SOURCE_REVISION, "patch_version": PATCH_VERSION,
                    "build_version": BUILD_VERSION, "target": current,
                    "patch_sha256": hashlib.sha256(server.read_bytes()).hexdigest(),
                    "binary_sha256": lfm._digest(binary)}
        (root / "live-build.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        os.chmod(root / "live-build.json", 0o600)
        print("Live voice worker built. No microphone was opened.", flush=True)


def verify(root: Path) -> Path:
    lfm.verify_weights(root)
    return verify_engine(root)


def installed(root: Path) -> bool:
    """Cheap compatibility check for UI polling; launch still verifies hashes."""
    try:
        metadata = json.loads((root / "live-build.json").read_text(encoding="utf-8"))
        return (isinstance(metadata, dict) and metadata.get("source_revision") == SOURCE_REVISION and metadata.get("patch_version") == PATCH_VERSION
                and metadata.get("build_version") == BUILD_VERSION and metadata.get("target") == target()
                and native_binary(root).is_file())
    except (OSError, ValueError):
        return False


def verify_engine(root: Path, expected_target: str | None = None) -> Path:
    current = expected_target or target()
    try:
        metadata = json.loads((root / "live-build.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise PrototypeError("Live voice metadata is invalid; run setup again.")
        binary = native_binary(root, current)
        # Check the patch implementation too, so a patch update requires rebuild.
        with tarfile.open(root / "live-source.tar.gz") as archive:
            if lfm._digest(root / "live-source.tar.gz") != SOURCE_SHA256:
                raise PrototypeError("Live source archive changed; rebuild the voice runtime.")
            original = archive.extractfile(f"llama.cpp-{SOURCE_REVISION}/tools/liquid-audio/server.cpp").read().decode()
        digest = hashlib.sha256(patched_server(original).encode()).hexdigest()
        if (metadata.get("source_revision") != SOURCE_REVISION or metadata.get("patch_version") != PATCH_VERSION
                or metadata.get("target") != current or metadata.get("build_version") != BUILD_VERSION
                or metadata.get("patch_sha256") != digest or binary.is_symlink()
                or metadata.get("binary_sha256") != lfm._digest(binary)):
            raise PrototypeError("Live voice worker changed; rebuild the voice runtime.")
        return binary
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        raise PrototypeError("Live voice runtime is missing or invalid; run the explicit build command.") from error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=4)
    arguments = parser.parse_args()
    install(arguments.runtime_dir.resolve(), cmake=arguments.cmake, jobs=arguments.jobs)
