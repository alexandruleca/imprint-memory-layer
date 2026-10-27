"""Secret redaction cost per chunk on CPU (ADR 0060).

The corpus is this checkout's tracked source and documentation, cut into
consecutive chunks of 1,000, 2,000 and 4,000 characters (the chunker's
typical range). Rules-only redaction runs over every chunk; with
``--privacy-filter`` the model labels ``--samples`` chunks of each size.

    python scripts/benchmark-redaction.py [--privacy-filter] [--samples N] [--out FILE]

The model comes from the data directory (``IMPRINT_DATA_DIR``) and is
downloaded there on first use. Threads follow ``model.threads``.
"""
import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SIZES = (1000, 2000, 4000)
SUFFIXES = (".py", ".go", ".md", ".ts", ".tsx", ".json", ".yml", ".toml")


def corpus() -> dict[int, list[str]]:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    texts = []
    for name in sorted(files):
        path = ROOT / name
        if path.suffix in SUFFIXES and path.is_file() and path.stat().st_size < 2_000_000:
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                pass
    return {size: [text[i:i + size] for text in texts for i in range(0, len(text) - size + 1, size)]
            for size in SIZES}


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"n": len(values), "median": statistics.median(values), "mean": statistics.fmean(values),
            "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], "max": ordered[-1]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--privacy-filter", action="store_true")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--out")
    args = parser.parse_args()
    from imprint import config, redaction
    chunks = corpus()
    result = {"platform": f"{platform.system()} {platform.machine()}", "python": platform.python_version(),
              "processor": platform.processor() or platform.machine(),
              "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
              "rules": {}}
    for size, texts in chunks.items():
        times, kinds = [], Counter()
        for text in texts:
            started = time.perf_counter()
            redacted = redaction.apply(text, redaction.rule_spans(text))
            times.append((time.perf_counter() - started) * 1000)
            kinds += redaction.kinds(redacted) - redaction.kinds(text)
        result["rules"][str(size)] = {"ms_per_chunk": summary(times), "redacted": dict(sorted(kinds.items()))}
    if args.privacy_filter:
        import onnxruntime
        from imprint import model_profiles, privacy_filter
        started = time.perf_counter()
        session, tokenizer = privacy_filter._load()
        pf = {"model": f"{model_profiles.PRIVACY_FILTER_REPO}@{model_profiles.PRIVACY_FILTER_REVISION}"
                       f"/{model_profiles.PRIVACY_FILTER_FILE}",
              "onnxruntime": onnxruntime.__version__, "threads": config.CPU_THREADS,
              "load_seconds_including_sha256": time.perf_counter() - started, "sizes": {}}
        privacy_filter.spans("warm up the session once")
        for size, texts in chunks.items():
            step = max(1, len(texts) // args.samples)
            sample = texts[::step][:args.samples]
            seconds, tokens = [], []
            for text in sample:
                tokens.append(len(tokenizer.encode(text, add_special_tokens=False).ids))
                started = time.perf_counter()
                privacy_filter.spans(text)
                seconds.append(time.perf_counter() - started)
            pf["sizes"][str(size)] = {"seconds_per_chunk": summary(seconds), "tokens_per_chunk": summary(tokens),
                                      "ms_per_token": 1000 * sum(seconds) / sum(tokens)}
        try:
            import resource
            pf["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except ImportError:  # Windows
            pass
        result["privacy_filter"] = pf
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
