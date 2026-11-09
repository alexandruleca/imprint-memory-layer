"""The local LLM's GGUF runtime. Importing this module never loads a model."""
from __future__ import annotations

import codecs
import json
import threading
import tempfile
import time
from pathlib import Path

from . import model_profiles as profiles

INFERENCE_LOCK = threading.RLock()
_load_lock = threading.RLock()
_models = {}


def download(repo: str, filename: str, target: Path, progress_cb=None) -> Path:
    """Serialize downloads across roles/processes and verify the default artifact."""
    import httpx
    from filelock import FileLock
    from huggingface_hub.utils import build_hf_headers
    from . import config
    from .downloads import verify_archive

    target.parent.mkdir(parents=True, exist_ok=True)
    pin = profiles.pinned(repo, filename)
    pinned = pin is not None
    revision, sha256 = pin if pin else ("main", None)
    with FileLock(str(target) + ".lock"):
        if target.exists():
            if pinned:
                verify_archive(target, sha256)
            return target
        fd, name = tempfile.mkstemp(dir=target.parent, suffix=".part")
        import os
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                # Same credentials as hf_hub_download. httpx drops the
                # Authorization header when a redirect leaves huggingface.co.
                with httpx.stream("GET", f"https://huggingface.co/{repo}/resolve/{revision}/{filename}",
                                  headers=build_hf_headers(token=config.hub_token()),
                                  follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length") or 0)
                    done = 0
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        stream.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            progress_cb(done, total)
            if pinned:
                verify_archive(tmp, sha256)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return target


def load(path: Path, n_ctx: int, n_gpu_layers: int, *, expected_sha256: str | None = None):
    """Reuse identical configurations in one process; serialize all inference."""
    key = (str(path.resolve()), n_ctx, n_gpu_layers)
    with _load_lock:
        if expected_sha256:
            from .downloads import verify_archive
            verify_archive(path, expected_sha256)
        if key in _models:
            return _models[key]
        from . import llama_runtime
        llm, _ = llama_runtime.load(key[0], n_gpu_layers, n_ctx=n_ctx, n_threads=4, n_threads_batch=4, n_batch=256)
        try:
            # Never deserialize a disk-backed prompt/state cache. The optional
            # binding still depends on unfixed diskcache (CVE-2025-69872).
            llm.set_cache(None)
            # Render the embedded template with thinking explicitly disabled.
            # Qwen3.5 does not implement the old /no_think prompt switch.
            from llama_cpp.llama_chat_format import Jinja2ChatFormatter
            template = llm.metadata.get("tokenizer.chat_template")
            if not template:
                raise ValueError("GGUF must include tokenizer.chat_template")
            def special(token_id):
                return llm.detokenize([token_id], special=True).decode() if token_id >= 0 else ""
            formatter = Jinja2ChatFormatter(
                template="{% set enable_thinking = false %}\n" + template,
                eos_token=special(llm.token_eos()), bos_token=special(llm.token_bos()),
                stop_token_ids=[llm.token_eos()],
            )
            llm.chat_handler = formatter.to_chat_handler()
            llm._imprint_formatter = formatter
            # Fail early if a custom template cannot render ordinary text.
            formatter(messages=[{"role": "user", "content": "Hello"}])
        except Exception:
            llm.close()
            raise
        _models[key] = llm
        return llm


def release(path: Path) -> None:
    """Forget every loaded configuration of ``path``. A caller still holding
    the model (a reply in progress) keeps it until it is done; the weights
    are freed when the last reference goes."""
    resolved = str(Path(path).resolve())
    with _load_lock:
        for key in [key for key in _models if key[0] == resolved]:
            del _models[key]


def fit_messages(llm, messages: list[dict], max_tokens: int, tools: list[dict] | None = None) -> list[dict]:
    """Bound actual token usage, dropping old conversation turns first."""
    messages = list(messages)
    while True:
        prompt = llm._imprint_formatter(messages=messages, tools=tools).prompt
        count = len(llm.tokenize(prompt.encode(), add_bos=False, special=True))
        if count + max_tokens + 16 <= llm.n_ctx():
            return messages
        # The current request is the last user message; tool results have
        # their own role. Keep it when pruning a long tool loop.
        anchor = max((i for i, message in enumerate(messages) if message["role"] == "user"),
                     default=len(messages) - 1)
        if anchor > 1:
            del messages[1]
            while len(messages) > 2 and messages[1]["role"] != "user":
                del messages[1]
            continue
        # Drop the loop's oldest assistant turn with its tool results, keeping the latest.
        end = anchor + 2
        while end < len(messages) and messages[end]["role"] == "tool":
            end += 1
        if end >= len(messages):
            raise ValueError("Local helper context exceeded; shorten the input or increase the configured context")
        del messages[anchor + 1:end]


def _before(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("local model time limit reached")


# Evaluated stable prompt prefixes kept in memory (ADR 0161). A hybrid model
# (Qwen3.5's linear-attention layers) cannot roll its context back to a
# shorter prefix, so every new chat turn recomputed the instructions and tool
# definitions; restoring a saved state skips them.
CHECKPOINTS = 2
CHECKPOINT_MIN_TOKENS = 256


class _Checkpoint:
    __slots__ = ("tokens", "data", "size")

    def __init__(self, tokens, data, size):
        self.tokens, self.data, self.size = tokens, data, size


def _common(a, b) -> int:
    import numpy as np
    n = min(len(a), len(b))
    if not n:
        return 0
    differ = np.flatnonzero(np.asarray(a[:n]) != np.asarray(b[:n]))
    return int(differ[0]) if len(differ) else n


def _rolls_back(llm) -> bool:
    """Whether llama.cpp can cut this model's live context back to any prefix."""
    return not (getattr(llm, "_is_hybrid", False) or getattr(llm, "_is_recurrent", False))


def _save(llm, tokens) -> _Checkpoint:
    """The context's state only. Llama.save_state also copies the logits
    array, n_batch × vocabulary floats (about 250 MB for Qwen3.5)."""
    import ctypes
    import llama_cpp
    ctx = llm._ctx.ctx
    size = int(llama_cpp.llama_state_get_size(ctx))
    data = (ctypes.c_uint8 * size)()
    written = int(llama_cpp.llama_state_get_data(ctx, data, size))
    if not 0 < written <= size:
        raise RuntimeError("could not copy the model state")
    return _Checkpoint(tuple(tokens), data, written)


def _restore(llm, checkpoint: _Checkpoint) -> None:
    import llama_cpp
    if int(llama_cpp.llama_state_set_data(llm._ctx.ctx, checkpoint.data, checkpoint.size)) != checkpoint.size:
        raise RuntimeError("could not restore the model state")
    count = len(checkpoint.tokens)
    llm.input_ids[:count] = checkpoint.tokens
    llm.n_tokens = count
    # The suffix evaluated next refreshes the logits before sampling.
    llm._requires_eval = True


def _stable_tokens(llm, stable: str, tools, tokens: list[int]) -> int:
    """How many leading prompt tokens the stable text and tools render to:
    the prompt's shared prefix with a rendering of only them (and a
    placeholder question, since some templates require one)."""
    try:
        head = llm._imprint_formatter(messages=[{"role": "system", "content": stable},
                                                {"role": "user", "content": "\x00"}], tools=tools).prompt
        return _common(llm.tokenize(head.encode(), add_bos=False, special=True), tokens)
    except Exception:
        return 0


def _resume(llm, tokens: list[int], stable: int) -> None:
    """Leave the context so that evaluating ``tokens`` starts as late as it can.

    llama.cpp keeps the live context's shared prefix when the prompt extends
    it, or for attention-only models any shared prefix. When a saved
    checkpoint covers more, it is restored. Otherwise, when the first
    ``stable`` tokens (the instructions and tools) are not kept, they are
    evaluated alone and saved before the rest. Any failure resets the context
    and drops the checkpoints: the prompt is then evaluated in full."""
    try:
        live = llm._input_ids
        common = _common(live, tokens)
        kept = common if common == len(live) or _rolls_back(llm) else 0
        saved = getattr(llm, "_imprint_checkpoints", [])
        # A checkpoint shorter than the prompt: the suffix evaluated after it
        # refreshes the logits, where an exact match would need a rollback.
        best = max((c for c in saved if kept < len(c.tokens) < len(tokens)
                    and _common(c.tokens, tokens) == len(c.tokens)), key=lambda c: len(c.tokens), default=None)
        if best is not None:
            _restore(llm, best)
            saved.remove(best)
            saved.insert(0, best)
            return
        if stable >= CHECKPOINT_MIN_TOKENS and stable > kept and stable < len(tokens):
            llm.reset()
            llm.eval(tokens[:stable])
            llm._imprint_checkpoints = [_save(llm, tokens[:stable]), *saved][:CHECKPOINTS]
    except Exception:
        llm._imprint_checkpoints = []
        try:
            llm.reset()
        except Exception:
            pass


def generate(llm, messages: list[dict], max_tokens: int, temperature: float, *,
             tools: list[dict] | None = None, schema: dict | None = None,
             deadline: float | None = None, stable: str | None = None):
    """Stream one assistant turn as text; the caller holds INFERENCE_LOCK.

    The model's chat template renders the messages and tools, and generation
    ends at an end-of-generation token. Special tokens are decoded as text,
    because tool-call grammars such as Mistral's [TOOL_CALLS] are written with
    them. A schema constrains the output with a grammar. A ``time.monotonic()``
    deadline raises TimeoutError instead of starting after it, or of
    generating past it. Prompt processing itself is not interruptible.

    ``stable`` is the start of the system message that later requests repeat
    (chat's instructions): the prompt up to its end, with the tools, is kept
    evaluated in memory and restored for them (ADR 0161)."""
    from llama_cpp import LlamaGrammar, llama_vocab_is_eog
    _before(deadline)
    messages = fit_messages(llm, messages, max_tokens, tools)
    prompt = llm._imprint_formatter(messages=messages, tools=tools).prompt
    grammar = LlamaGrammar.from_json_schema(json.dumps(schema), verbose=False) if schema else None
    text = codecs.getincrementaldecoder("utf-8")(errors="replace")
    prompt_tokens = llm.tokenize(prompt.encode(), add_bos=False, special=True)
    if (stable and hasattr(llm, "_ctx") and messages and messages[0].get("role") == "system"
            and messages[0]["content"].startswith(stable)):
        _resume(llm, prompt_tokens, _stable_tokens(llm, stable, tools, prompt_tokens))
    tokens = llm.generate(prompt_tokens,
                          temp=temperature, top_k=40, top_p=0.95, min_p=0.05, repeat_penalty=1.1,
                          grammar=grammar)
    try:
        for count, token in enumerate(tokens):
            if count == max_tokens or llama_vocab_is_eog(llm._model.vocab, token):
                return
            _before(deadline)
            piece = text.decode(llm.detokenize([token], special=True))
            if piece:
                yield piece
    finally:
        tokens.close()


def complete(llm, full_input: str, max_tokens: int = 512, schema: dict | None = None,
             deadline: float | None = None) -> str:
    """One bounded chat turn. A schema constrains the output with a grammar;
    without one the caller validates the text. The deadline is generate's."""
    with INFERENCE_LOCK:
        return "".join(generate(llm, [{"role": "user", "content": full_input}], max_tokens, 0.1,
                                schema=schema, deadline=deadline))


# Several short requests decoded as one batch (ADR 0165). Generating a token
# reads all of the model's weights whichever number of sequences it serves,
# so on a GPU several sequences cost little more per step than one.
BATCH_SEQUENCE_TOKENS = 4096  # Each sequence's context: a tagging prompt and its reply.
_BATCH_TOKENS = 512


class _BatchContext:
    """A second context on a loaded model, with one sequence per slot."""
    __slots__ = ("slots", "context", "batch")

    def __init__(self, slots, context, batch):
        self.slots, self.context, self.batch = slots, context, batch

    def close(self) -> None:
        self.batch.close()
        self.context.close()


def _batch_context(llm, slots: int) -> _BatchContext:
    """The model's batch context for ``slots`` sequences, made on first use.
    It shares the loaded weights; the context's own memory is the sequences'
    attention cache and recurrent state."""
    held = getattr(llm, "_imprint_batch", None)
    if held is not None and held.slots == slots:
        return held
    if held is not None:
        llm._imprint_batch = None
        held.close()
    import llama_cpp
    from llama_cpp import _internals
    params = llama_cpp.llama_context_params.from_buffer_copy(llm.context_params)
    params.n_ctx = slots * BATCH_SEQUENCE_TOKENS
    params.n_batch = params.n_ubatch = _BATCH_TOKENS
    params.n_seq_max = slots
    params.kv_unified = False  # Each sequence its own BATCH_SEQUENCE_TOKENS.
    # The context frees itself before the model it is made on.
    context = _internals.LlamaContext(model=llm._model, params=params, verbose=False)
    batch = _internals.LlamaBatch(n_tokens=_BATCH_TOKENS, embd=0, n_seq_max=1, verbose=False)
    held = _BatchContext(slots, context, batch)
    llm._imprint_batch = held
    return held


def _fill(batch, entries) -> None:
    """Put (sequence, position, token, wants logits) entries in a batch."""
    raw = batch.batch
    raw.n_tokens = len(entries)
    for i, (seq, pos, token, logits) in enumerate(entries):
        raw.token[i], raw.pos[i], raw.n_seq_id[i], raw.logits[i] = token, pos, 1, logits
        raw.seq_id[i][0] = seq


def _sampler(llm):
    """The sampling ``generate`` uses at temperature 0.1, one per sequence:
    the repetition penalty keeps each sequence's own history."""
    import llama_cpp
    from llama_cpp import _internals
    sampler = _internals.LlamaSampler()
    sampler.add_penalties(n_vocab=llm._n_vocab, penalty_last_n=llm.last_n_tokens_size, penalty_repeat=1.1,
                          penalty_freq=0.0, penalty_present=0.0)
    sampler.add_top_k(40)
    sampler.add_typical(1.0, 1)
    sampler.add_top_p(0.95, 1)
    sampler.add_min_p(0.05, 1)
    sampler.add_temp(0.1)
    sampler.add_dist(llama_cpp.LLAMA_DEFAULT_SEED)
    return sampler


def _decode_group(llm, held: _BatchContext, prompts: list[list[int]], max_tokens: int) -> list[str]:
    """Generate one reply per prompt, each in its own sequence. The prompts
    are prefilled together, then every step decodes one token of each
    sequence still generating."""
    import llama_cpp
    memory = llama_cpp.llama_get_memory(held.context.ctx)
    for seq in range(len(prompts)):
        llama_cpp.llama_memory_seq_rm(memory, seq, -1, -1)
    samplers = [_sampler(llm) for _ in prompts]
    try:
        decoders = [codecs.getincrementaldecoder("utf-8")(errors="replace") for _ in prompts]
        texts: list[list[str]] = [[] for _ in prompts]
        position = [len(prompt) for prompt in prompts]
        pending: dict[int, int] = {}
        queued = [(seq, i, token, i == len(prompt) - 1)
                  for seq, prompt in enumerate(prompts) for i, token in enumerate(prompt)]
        for start in range(0, len(queued), _BATCH_TOKENS):
            chunk = queued[start:start + _BATCH_TOKENS]
            _fill(held.batch, chunk)
            held.context.decode(held.batch)
            # Logits belong to the last decode: sample each sequence that ended here now.
            for index, (seq, _, _, last) in enumerate(chunk):
                if last:
                    pending[seq] = samplers[seq].sample(held.context, index)
        produced = [0] * len(prompts)
        vocab = llm._model.vocab
        while pending:
            step = []
            for seq, token in sorted(pending.items()):
                if llama_cpp.llama_vocab_is_eog(vocab, token):
                    continue
                texts[seq].append(decoders[seq].decode(llm.detokenize([token], special=True)))
                produced[seq] += 1
                if produced[seq] < max_tokens:
                    step.append((seq, position[seq], token, True))
                    position[seq] += 1
            pending = {}
            if not step:
                break
            _fill(held.batch, step)
            held.context.decode(held.batch)
            for index, (seq, _, _, _) in enumerate(step):
                pending[seq] = samplers[seq].sample(held.context, index)
        return ["".join(parts) + decoder.decode(b"", final=True) for parts, decoder in zip(texts, decoders)]
    finally:
        for sampler in samplers:
            sampler.close()


def complete_many(llm, inputs: list[str], max_tokens: int, slots: int) -> list:
    """``complete(llm, text, max_tokens)`` for each input, up to ``slots`` of
    them decoded as one batch. Returns, in order, each reply or the exception
    its request raised.

    An input whose prompt does not fit a batch sequence, or a batch the GPU
    cannot hold, is completed on its own instead, the way ``complete`` does."""
    results: list = [None] * len(inputs)
    alone: list[int] = []
    batched: list[tuple[int, list[int]]] = []
    with INFERENCE_LOCK:
        if slots > 1 and not getattr(llm, "_imprint_unbatched", False):
            for index, text in enumerate(inputs):
                try:
                    prompt = llm._imprint_formatter(messages=[{"role": "user", "content": text}]).prompt
                    tokens = list(llm.tokenize(prompt.encode(), add_bos=False, special=True))
                except Exception:
                    tokens = []
                if tokens and len(tokens) + max_tokens < BATCH_SEQUENCE_TOKENS:
                    batched.append((index, tokens))
                else:
                    alone.append(index)
        else:
            alone = list(range(len(inputs)))
        for start in range(0, len(batched), max(slots, 1)):
            group = batched[start:start + slots]
            if len(group) == 1:
                alone.append(group[0][0])
                continue
            try:
                held = _batch_context(llm, slots)
                replies = _decode_group(llm, held, [tokens for _, tokens in group], max_tokens)
            except Exception as error:
                # Most likely the GPU cannot hold the batch's context: this
                # process stops trying and completes requests one at a time.
                import sys
                print(f"imprint: batched generation failed, completing one at a time ({error})", file=sys.stderr)
                held = getattr(llm, "_imprint_batch", None)
                llm._imprint_batch, llm._imprint_unbatched = None, True
                if held is not None:
                    held.close()
                alone.extend(index for index, _ in batched[start:])
                break
            for (index, _), reply in zip(group, replies):
                results[index] = reply
        for index in sorted(alone):
            try:
                results[index] = complete(llm, inputs[index], max_tokens=max_tokens)
            except Exception as error:
                results[index] = error
    return results
