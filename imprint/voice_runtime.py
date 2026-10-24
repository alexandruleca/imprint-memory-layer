"""Explicit, isolated CPU build for the live LFM voice pilot (ADR 0043)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
PATCH_VERSION = 1
SERVER = "llama-liquid-audio-server"


def patched_server(source: str) -> str:
    """Fail closed if the pinned upstream source contract changes."""
    def replace(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise PrototypeError("Live voice source patch does not match the pinned revision.")
        source = source.replace(old, new)

    replace("    common_init();", '''    const char * secret = std::getenv("IMPRINT_VOICE_BACKEND_TOKEN");
    if (!secret || std::string(secret).size() < 32) { return 1; }
    const std::string authorization = "Bearer " + std::string(secret);
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


def install(root: Path, *, cmake: str = "cmake", jobs: int = 4) -> None:
    lfm._platform()
    if not 1 <= jobs <= 16:
        raise PrototypeError("Build jobs must be 1–16.")
    executable = shutil.which(cmake)
    if not executable:
        raise PrototypeError("Install CMake and a C++ compiler, or pass --cmake /path/to/cmake.")
    lfm.install(root)
    from filelock import FileLock
    with FileLock(str(root / ".live-build.lock")):
        archive = root / "live-source.tar.gz"
        if not archive.is_file() or lfm._digest(archive) != SOURCE_SHA256:
            url = f"https://codeload.github.com/tdakhran/llama.cpp/tar.gz/{SOURCE_REVISION}"
            fd, name = tempfile.mkstemp(dir=root)
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
        source_root = root / "live-source"
        source_root.mkdir(mode=0o700, exist_ok=True)
        with tarfile.open(archive) as source:
            source.extractall(source_root, filter="data")
        tree = source_root / f"llama.cpp-{SOURCE_REVISION}"
        server = tree / "tools/liquid-audio/server.cpp"
        server.write_text(patched_server(server.read_text()))
        build = root / "live-build"
        subprocess.run([executable, "-S", str(tree), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                        "-DGGML_NATIVE=OFF", "-DGGML_CUDA=OFF", "-DGGML_VULKAN=OFF",
                        "-DBUILD_SHARED_LIBS=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_OPENSSL=OFF",
                        "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF"], check=True)
        subprocess.run([executable, "--build", str(build), "--target", SERVER, "-j", str(jobs)], check=True)
        binary = build / "bin" / SERVER
        metadata = {"source_revision": SOURCE_REVISION, "patch_version": PATCH_VERSION,
                    "patch_sha256": hashlib.sha256(server.read_bytes()).hexdigest(),
                    "binary_sha256": lfm._digest(binary)}
        (root / "live-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
        os.chmod(root / "live-build.json", 0o600)
        print("Live voice worker built. No microphone was opened.", flush=True)


def verify(root: Path) -> Path:
    lfm.verify_runtime(root)
    try:
        metadata = json.loads((root / "live-build.json").read_text())
        binary = root / "live-build/bin" / SERVER
        # Check the patch implementation too, so a patch update requires rebuild.
        with tarfile.open(root / "live-source.tar.gz") as archive:
            if lfm._digest(root / "live-source.tar.gz") != SOURCE_SHA256:
                raise PrototypeError("Live source archive changed; rebuild the voice runtime.")
            original = archive.extractfile(f"llama.cpp-{SOURCE_REVISION}/tools/liquid-audio/server.cpp").read().decode()
        digest = hashlib.sha256(patched_server(original).encode()).hexdigest()
        if (metadata.get("source_revision") != SOURCE_REVISION or metadata.get("patch_version") != PATCH_VERSION
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
