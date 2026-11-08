"""The local model keeps chat's stable prompt prefix evaluated (ADR 0161)."""
import unittest
from unittest.mock import patch

from imprint import local_llm


class FakeLlama:
    """The parts of llama_cpp.Llama that _resume touches."""

    def __init__(self, hybrid=True):
        self._ctx = object()
        self._is_hybrid = hybrid
        self.input_ids = [0] * 10_000
        self.n_tokens = 0
        self.evaluated = []

    @property
    def _input_ids(self):
        return self.input_ids[:self.n_tokens]

    def reset(self):
        self.n_tokens = 0

    def eval(self, tokens):
        self.evaluated.append(len(tokens))
        self.input_ids[self.n_tokens:self.n_tokens + len(tokens)] = tokens
        self.n_tokens += len(tokens)

    def live(self, tokens):
        self.input_ids[:len(tokens)] = tokens
        self.n_tokens = len(tokens)


def saved(llm, tokens):
    return local_llm._Checkpoint(tuple(tokens), b"state", 5)


def restored(llm, checkpoint):
    llm.live(list(checkpoint.tokens))


STABLE = list(range(1, 401))  # instructions and tools, 400 tokens


class ResumeTests(unittest.TestCase):
    def setUp(self):
        patches = [patch.object(local_llm, "_save", side_effect=saved),
                   patch.object(local_llm, "_restore", side_effect=restored)]
        self.save, self.restore = (p.start() for p in patches)
        for p in patches:
            self.addCleanup(p.stop)

    def test_the_first_turn_evaluates_the_stable_prefix_alone_and_saves_it(self):
        llm = FakeLlama()
        local_llm._resume(llm, STABLE + [900, 901], 400)
        self.assertEqual(llm.evaluated, [400])
        self.assertEqual([len(c.tokens) for c in llm._imprint_checkpoints], [400])
        self.restore.assert_not_called()

    def test_a_new_turn_on_a_hybrid_model_restores_the_checkpoint(self):
        llm = FakeLlama()
        local_llm._resume(llm, STABLE + [900, 901], 400)
        # The reply went on: the live context now diverges from the next prompt
        # after the stable prefix, which a hybrid model cannot cut back to.
        llm.live(STABLE + [900, 901, 950, 951])
        local_llm._resume(llm, STABLE + [900, 901, 777], 400)
        self.restore.assert_called_once()
        self.assertEqual(llm.evaluated, [400])  # Not evaluated again.
        self.assertEqual(llm.n_tokens, 400)

    def test_a_prompt_that_extends_the_live_context_keeps_it(self):
        llm = FakeLlama()
        llm.live(STABLE + [900, 901])
        local_llm._resume(llm, STABLE + [900, 901, 902], 400)
        self.save.assert_not_called()
        self.restore.assert_not_called()
        self.assertEqual(llm.n_tokens, 402)

    def test_an_attention_model_keeps_a_shared_prefix_that_covers_the_stable_part(self):
        llm = FakeLlama(hybrid=False)
        llm.live(STABLE + [900, 901])
        local_llm._resume(llm, STABLE + [777], 400)
        self.save.assert_not_called()
        self.restore.assert_not_called()

    def test_a_short_stable_prefix_is_not_checkpointed(self):
        llm = FakeLlama()
        local_llm._resume(llm, list(range(1, 101)) + [900], 100)
        self.save.assert_not_called()
        self.assertEqual(llm.evaluated, [])

    def test_a_checkpoint_as_long_as_the_prompt_is_not_restored(self):
        llm = FakeLlama()
        llm._imprint_checkpoints = [local_llm._Checkpoint(tuple(STABLE), b"", 1)]
        local_llm._resume(llm, list(STABLE), 400)
        self.restore.assert_not_called()

    def test_changed_instructions_save_a_new_checkpoint_and_keep_two(self):
        llm = FakeLlama()
        for first in (1000, 2000, 3000):
            local_llm._resume(llm, [first] + STABLE[1:] + [900], 400)
        self.assertEqual([c.tokens[0] for c in llm._imprint_checkpoints], [3000, 2000])
        llm.live([5])
        local_llm._resume(llm, [2000] + STABLE[1:] + [901], 400)
        self.restore.assert_called_once()
        self.assertEqual([c.tokens[0] for c in llm._imprint_checkpoints], [2000, 3000])

    def test_a_failure_drops_the_checkpoints_and_resets_the_context(self):
        llm = FakeLlama()
        llm._imprint_checkpoints = [local_llm._Checkpoint((9,) * 300, b"", 1)]
        llm.live([1, 2, 3])
        self.save.side_effect = RuntimeError("no state")
        local_llm._resume(llm, STABLE + [900], 400)
        self.assertEqual(llm._imprint_checkpoints, [])
        self.assertEqual(llm.n_tokens, 0)


class StableTokensTests(unittest.TestCase):
    def test_the_stable_part_is_the_prompts_shared_prefix_with_its_rendering(self):
        class Llm:
            def _imprint_formatter(self, messages, tools=None):
                return type("R", (), {"prompt": "|".join(m["content"] for m in messages)})()

            def tokenize(self, data, add_bos=False, special=True):
                return list(data)

        prompt = list(b"SYSTEM TEXT plus summary|question")
        self.assertEqual(local_llm._stable_tokens(Llm(), "SYSTEM TEXT", None, prompt), len("SYSTEM TEXT"))

    def test_a_template_that_fails_gives_no_stable_part(self):
        class Llm:
            def _imprint_formatter(self, messages, tools=None):
                raise ValueError("template")

        self.assertEqual(local_llm._stable_tokens(Llm(), "x", None, [1, 2]), 0)


if __name__ == "__main__":
    unittest.main()
