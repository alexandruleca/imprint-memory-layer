#!/usr/bin/env python3
"""Opt-in real GGUF check. Needs the optional LLM profile and several GB of free RAM/disk.

Run with PYTHONPATH=. and the provisioned Python. --model-path reuses a pinned
Qwen3.5-4B-Q4_K_M.gguf; otherwise downloads into disposable storage. No user
settings, memories or client profiles are changed. CPU inference only.
"""
import argparse
import gc
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='imprint-helper-smoke-') as directory:
        os.environ['IMPRINT_DATA_DIR'] = directory
        os.environ['IMPRINT_CHAT_N_GPU_LAYERS'] = '0'
        os.environ['IMPRINT_TAGGER_N_GPU_LAYERS'] = '0'
        os.environ['IMPRINT_CHAT_N_CTX'] = '8192'
        os.environ['IMPRINT_TAGGER_N_CTX'] = '8192'
        os.environ['IMPRINT_CHAT_PROVIDER'] = 'local'
        os.environ['IMPRINT_LLM_TAGGER_PROVIDER'] = 'local'
        os.environ['IMPRINT_SUMMARIZER_PROVIDER'] = 'local'
        if args.model_path:
            from imprint.downloads import verify_archive
            from imprint.model_profiles import HELPER_SHA256
            verify_archive(args.model_path, HELPER_SHA256)
            os.environ['IMPRINT_CHAT_MODEL_PATH'] = str(args.model_path.resolve())
            os.environ['IMPRINT_TAGGER_MODEL_PATH'] = str(args.model_path.resolve())
        from imprint import chat, tagger, local_llm
        from imprint.cli_summarize import _call_summarizer_llm, _SUMMARIZER_PROMPT
        model = None
        try:
            model, error = chat.load_model()
            assert error is None, error
            other, error = tagger._load_tagger_model()
            assert error is None and other is model, error
            prompt = model._imprint_formatter(messages=[{'role': 'user', 'content': 'Hello'}]).prompt
            assert prompt.endswith('<think>\n\n</think>\n\n'), prompt[-100:]
            taxonomy = tagger._LLM_CLASSIFY_PROMPT.format(
                types='decision, pattern, bug, finding, architecture, preference', domains='infra, build, testing')
            result = json.loads(tagger._call_llm_raw_text(taxonomy +
                'We decided to validate Windows installers on native CI because cross-compilation cannot test installation.'))
            assert set(result) == set(tagger._CLASSIFY_FALLBACK), result
            assert result['type'] == 'decision', result
            print('Shared helper, non-thinking template and schema classification passed', flush=True)
            result = json.loads(_call_summarizer_llm(_SUMMARIZER_PROMPT +
                'USER: We decided to use native Windows CI for installer validation.\n'
                'ASSISTANT: This catches actual install failures that cross-compilation misses.'))
            assert set(result) == {'decisions', 'bugs', 'patterns', 'facts', 'summary'}, result
            assert result['decisions'] and result['summary'], result
            print('Local structured summary passed', flush=True)
            with patch.dict(chat.TOOLS, {'status': chat.TOOLS['status']._replace(run=lambda **kwargs: '{"total_memories": 7}')}):
                events = list(chat.stream_reply([], 'Use the status tool to count my memories, then report the count.'))
            assert any(e['type'] == 'tool_call' and e['name'] == 'status' for e in events), events
            assert not any(e['type'] == 'error' for e in events), events
            assert any(e['type'] == 'assistant_message' and '7' in e['text'] for e in events), events
            print('Dashboard tool call/result/answer passed (mocked storage)', flush=True)
        finally:
            # Release mappings before TemporaryDirectory cleanup on Windows.
            if model is not None:
                model.close()
            chat._llm = tagger._tagger_llm = None
            local_llm._models.clear()
            gc.collect()


if __name__ == '__main__':
    main()
