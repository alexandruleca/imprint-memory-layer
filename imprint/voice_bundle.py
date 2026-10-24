"""Release-bundled private speech engine; no end-user compiler or CMake."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

from . import lfm_audio as lfm, voice_runtime as runtime
from .personaplex import PrototypeError


def bundled_root() -> Path:
    return Path(__file__).resolve().parent / "voice-engine"


def available() -> bool:
    return (bundled_root() / "live-build.json").is_file()


def export_engine(root: Path, destination: Path) -> None:
    binary = runtime.verify_engine(root)
    if destination.exists():
        raise PrototypeError("Choose a new engine bundle destination")
    destination.mkdir(parents=True, mode=0o700)
    for source in [binary, root / "live-source.tar.gz", root / "live-build.json"]:
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (destination / "NOTICE.md").write_text(
        "Imprint private LFM speech engine\n\n"
        f"Built from tdakhran/llama.cpp {runtime.SOURCE_REVISION} with Imprint's private-listener patch.\n"
        "llama.cpp and ggml use the MIT license. Full pinned upstream source and third-party notices,\n"
        "including cpp-httplib and json.hpp, are provided in live-source.tar.gz.\n"
        "Model weights are not included. LFM model licensing is separate.\n", encoding="utf-8")
    runtime.verify_engine(destination)


def install(root: Path, *, progress=None, download_prefix="") -> None:
    source = bundled_root()
    binary = runtime.verify_engine(source)
    if not (source / "NOTICE.md").is_file():
        raise PrototypeError("Bundled engine notices are missing")
    lfm.install_weights(root, progress=progress, download_prefix=download_prefix)
    from filelock import FileLock
    with FileLock(str(root / ".live-build.lock")):
        if progress: progress({"stage": "preparing"})
        # Metadata is installed last. Every file is replaced atomically and the
        # launch-time verifier still checks source, patch, target and binary.
        for item in [binary, source / "live-source.tar.gz", source / "NOTICE.md", source / "live-build.json"]:
            target = root / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=root, prefix=download_prefix, suffix=".part")
            os.close(fd)
            try:
                shutil.copyfile(item, name)
                os.chmod(name, 0o700 if item == binary else 0o600)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        if progress: progress({"stage": "finishing"})
        runtime.verify(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--cmake", default="cmake")
    args = parser.parse_args()
    runtime.build_engine(args.build_root.resolve(), jobs=args.jobs, cmake=args.cmake)
    export_engine(args.build_root.resolve(), args.output.resolve())
