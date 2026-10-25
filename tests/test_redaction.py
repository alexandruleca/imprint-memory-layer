"""Secrets are redacted before memories and facts are stored (ADR 0060)."""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from qdrant_client import models

import test_indexing_retrieval as indexing
from imprint import config, config_schema, imprint_graph as kg, privacy_filter, redaction, sync_ws, vectorstore as vs

# Fixtures are assembled from parts so this file holds no credential-shaped
# literal for secret scanners to flag.
A = "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3"
SECRETS = {
    "aws-access-key": "AKIA" + "IOSFODNN7EXAMPLE",
    "github-token": "ghp_" + A[:36],
    "gitlab-token": "glpat-" + A[:20],
    "openai-key": "sk-proj-" + A + "_" + A + "T3BlbkFJ" + A,
    "anthropic-key": "sk-ant-api03-" + (A * 3)[:93] + "AA",
    "huggingface-token": "hf_" + A[:34],
    "slack-token": "xoxb-" + "1234567890123-1234567890123-" + A[:24],
    "slack-webhook": "https://hooks.slack.com/services/" + "T00000000/B00000000/" + A[:24],
    "stripe-key": "sk_live_" + A[:24],
    "google-api-key": "AIza" + "Sy" + A[:33],
    "google-oauth-secret": "GOCSPX-" + A[:28],
    "npm-token": "npm_" + A[:36],
    "pypi-token": "pypi-AgEIcHlwaS5vcmc" + (A * 2)[:60],
    "sendgrid-key": "SG." + A[:22] + "." + (A * 2)[:43],
    "jwt": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}
PEM_BODY = "MIIEowIBAAKCAQEA" + A + "\n" + A + A[:20] + "==\n"
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\n" + PEM_BODY + "-----END RSA " + "PRIVATE KEY-----"
CUT = PEM.split("-----END")[0] + "Qy"
TAIL = PEM[PEM.index("\n") + 30:]


def R(kind, secret):
    """The placeholder that replaces ``secret``."""
    return f"[REDACTED:{kind}:{len(secret)}]"


TRUE_POSITIVES = [
    *[(f"value {secret} end", f"value {R(kind, secret)} end") for kind, secret in SECRETS.items()],
    (f"key:\n{PEM}\nafter", f"key:\n{R('private-key', PEM)}\nafter"),
    # A key cut by a chunk boundary, either side, and one inside a JSON string.
    ("cfg:\n" + CUT, "cfg:\n" + R("private-key", CUT)),
    (TAIL + "\nThe end.", R("private-key", TAIL) + "\nThe end."),
    (json.dumps({"private_key": PEM + "\n", "n": 1}), json.dumps({"private_key": "@@\n", "n": 1}).replace(
        "@@", R("private-key", json.dumps(PEM)[1:-1]))),
    ("DATABASE_URL=postgres://admin:" + "S3cr3t!pass@db.example.com:5432/app",
     f"DATABASE_URL=postgres://admin:{R('password', 'S3cr3t!pass')}@db.example.com:5432/app"),
    ("redis://:" + "Tr0ub4dor@cache:6379/0", f"redis://:{R('password', 'Tr0ub4dor')}@cache:6379/0"),
    ('password = "' + 'hunter2!x"', f'password = "{R("password", "hunter2!x")}"'),
    ('{"password": "' + 'Tr0ub4dor&3", "user": "bob"}', f'{{"password": "{R("password", "Tr0ub4dor&3")}", "user": "bob"}}'),
    ("DB_PASSWORD=" + "correcthorsebatterystaple\nAPI_SECRET=" + "9f8e7d6c5b4a39281706",
     f"DB_PASSWORD={R('password', 'correcthorsebatterystaple')}\nAPI_SECRET={R('secret', '9f8e7d6c5b4a39281706')}"),
    ("clientSecret: '" + "q8Zr2vLx7KpN4wTb'", f"clientSecret: '{R('secret', 'q8Zr2vLx7KpN4wTb')}'"),
    ("Authorization: Basic " + "dXNlcjpwYXNzd29yZA==", f"Authorization: Basic {R('basic-auth', 'dXNlcjpwYXNzd29yZA==')}"),
    ("curl -H 'Authorization: Bearer " + "8f14e45fceea167a5a36dedd4bea2543'",
     f"curl -H 'Authorization: Bearer {R('bearer-token', '8f14e45fceea167a5a36dedd4bea2543')}'"),
    # A token assigned to a credential name keeps its specific kind.
    (f"OPENAI_API_KEY={SECRETS['openai-key']}", f"OPENAI_API_KEY={R('openai-key', SECRETS['openai-key'])}"),
]

FALSE_POSITIVES = [
    "commit 1f3c9e2b7a6d5c4b3a29180f7e6d5c4b3a291807\nMerge: 5d0a1f2 9e8d7c6",
    "id: 123e4567-e89b-12d3-a456-426614174000 session_id = 'a8098c1a-f86e-11da-bd1a-00112444be1e'",
    '"integrity": "sha512-Qqw0ZZb8Fc8rB0G1s1qWJ9nL0Xx6ql4c1BmrX1Y5PZ1bP4Tw9bm2Gz+5x1lVQm3nUn0r0XH2Tw==",',
    'wheels = [{ url = "https://files.pythonhosted.org/p/x.whl", hash = "sha256:'
    '5d0a1f2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f", size = 123 }]',
    "golang.org/x/text v0.3.7 h1:olpwvP2KacW1ZWvsR7uQhoyTYvKAupfQrRGBFM352Gk=",
    "pkg==1.0 --hash=sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "![dot](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==)",
    'password = os.environ["DB_PASSWORD"]\ntoken = request.headers.get("Authorization")',
    "def login(username: str, password: str) -> Token:\n    api_key: str = Field(default='')",
    "self.secret_key = settings.SECRET_KEY\nmax_tokens = 1024\ntokenizer.pad_token = \"<|endoftext|>\"",
    "if password == confirm_password:\n    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())",
    "const token = await getToken();\napiKey: process.env.OPENAI_API_KEY,\npassword: string;",
    "env:\n  - name: DB_PASSWORD\n    valueFrom:\n      secretKeyRef:\n        name: db-secret\n        key: password",
    "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\nOPENAI_API_KEY=<your key>\nexport API_TOKEN=your-token-here",
    '"password": "Password",\n"forgot_password": "Forgot your password?",\n"password_hint": "Enter password"',
    "PWD=/home/user/project\nOLDPWD=/home/user\nPOSTGRES_PASSWORD: example",
    "Rotate the token every 90 days. The secret is stored in Vault. Bearer authentication is required.",
    "conn = f\"postgres://{user}:{password}@{host}/db\" and https://github.com:443/org/repo@main",
    "Connect with scheme://user:password@host, e.g. postgres://app:secret@localhost/db",
    "arn:aws:iam::123456789012:role/Admin and sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "pk_live_" + A[:24] + " is a publishable key; scikit-learn uses sk-learn style names",
    "password VARCHAR(255) NOT NULL, secret BOOLEAN DEFAULT false, token TEXT",
]


class RuleTests(unittest.TestCase):
    def test_each_supported_format_is_redacted(self):
        for text, expected in TRUE_POSITIVES:
            with self.subTest(text=text[:40]):
                self.assertEqual(redaction.apply(text, redaction.rule_spans(text)), expected)

    def test_ordinary_code_and_data_are_left_alone(self):
        for text in FALSE_POSITIVES:
            with self.subTest(text=text[:40]):
                self.assertEqual(redaction.rule_spans(text), [])

    def test_redacting_redacted_text_changes_nothing(self):
        for text, _ in TRUE_POSITIVES:
            once = redaction.apply(text, redaction.rule_spans(text))
            self.assertEqual(redaction.apply(once, redaction.rule_spans(once)), once)

    def test_kinds_count_the_placeholders(self):
        text = redaction.apply(f"a {SECRETS['jwt']} b {SECRETS['npm-token']} c {SECRETS['jwt']}",
                               redaction.rule_spans(f"a {SECRETS['jwt']} b {SECRETS['npm-token']} c {SECRETS['jwt']}"))
        self.assertEqual(redaction.kinds(text), {"jwt": 2, "npm-token": 1})

    def test_a_further_redaction_supersedes_each_earlier_version(self):
        token = SECRETS['github-token']
        loose = f"use {token} with SECRET"
        rules = f"use {R('github-token', token)} with SECRET"
        strict = f"use {R('github-token', token)} with {R('secret', 'SECRET')}"
        for newer, older in ((rules, loose), (strict, rules), (strict, loose)):
            self.assertTrue(redaction.supersedes(newer, older))
            self.assertFalse(redaction.supersedes(older, newer))
        self.assertFalse(redaction.supersedes(strict, strict))
        # Another secret of the same length is the same memory; any other change is not.
        self.assertTrue(redaction.supersedes(rules, f"use {'ghp_' + A[1:37]} with SECRET"))
        self.assertFalse(redaction.supersedes(rules, f"use {token[:-1]} with SECRET"))
        self.assertFalse(redaction.supersedes(rules, f"use {token} with SECRETS"))
        self.assertFalse(redaction.supersedes(strict, f"use {token} with {R('password', 'SECRET')}"))

    def test_large_encoded_blobs_stay_fast(self):
        # Quadratic matching would take minutes on these; linear takes well under a second.
        blob = "data:application/octet-stream;base64," + ("QUJDREVGR0hJSktMTU5PUFFSU1RVVldY" * 6000)
        lines = "\n".join(("TUlJRXZRSUJBREFOQmdrcWhraUc5dzBCQVFFRkFBU0NCS2N3Z2dTakFnRUFBb0lCQVFD" for _ in range(5000)))
        end = "-----END " + "PRIVATE KEY-----"
        for text in (blob, lines, lines + "\n" + end, "-----BEGIN " + "PRIVATE KEY-----\n" + lines + " !", "a" * 200000):
            started = time.perf_counter()
            redaction.rule_spans(text)
            self.assertLess(time.perf_counter() - started, 2.5)


class SettingTests(unittest.TestCase):
    def test_privacy_filter_is_off_by_default_and_follows_env_over_config(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'IMPRINT_DATA_DIR': data}):
            os.environ.pop('IMPRINT_REDACT_PRIVACY_FILTER', None)
            self.assertEqual(config_schema.resolve('redact.privacy_filter'), (False, 'default'))
            config_schema.set_value('redact.privacy_filter', 'true')
            self.assertEqual(config_schema.resolve('redact.privacy_filter'), (True, 'config'))
            with patch.dict(os.environ, {'IMPRINT_REDACT_PRIVACY_FILTER': '0'}):
                self.assertEqual(config_schema.resolve('redact.privacy_filter'), (False, 'env'))


# ── Privacy Filter with a stand-in model ────────────────────────
LABELS = {"SECRET": 32, "ACCT": 4, "PERSON": 20, "BSECRET": 29, "ISECRET": 30, "ESECRET": 31}


class FakeTokenizer:
    """One token per word or punctuation mark; its id is its label in the fake model."""

    def encode(self, text, add_special_tokens=True):
        assert not add_special_tokens
        tokens = [match.span() for match in re.finditer(r"\s*(?:\w+|[^\w\s])", text)]

        class Encoding:
            offsets = tokens
            ids = [LABELS.get(text[a:b].strip(), 0) for a, b in tokens]
        return Encoding()


class FakeSession:
    def __init__(self):
        self.windows = []

    def run(self, names, inputs):
        assert names == ["logits"] and inputs["attention_mask"].shape == inputs["input_ids"].shape
        ids = inputs["input_ids"][0]
        self.windows.append(len(ids))
        logits = np.zeros((1, len(ids), 33), dtype=np.float32)
        logits[0, np.arange(len(ids)), ids] = 12.0
        return [logits]


class PrivacyFilterTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        loaded = patch.object(privacy_filter, "_load", return_value=(self.session, FakeTokenizer()))
        loaded.start()
        self.addCleanup(loaded.stop)
        enabled = patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_only_secrets_and_account_numbers_are_redacted(self):
        text = "PERSON paid with ACCT, then typed SECRET. Tokens BSECRET ISECRET ESECRET end"
        self.assertEqual(redaction.redact(text), f"PERSON paid with {R('account-number', 'ACCT')}, then typed "
                                                 f"{R('secret', 'SECRET')}. Tokens {R('secret', 'BSECRET ISECRET ESECRET')} end")

    def test_rules_run_first_and_their_placeholders_are_kept(self):
        text = f"key {SECRETS['github-token']} and SECRET"
        self.assertEqual(redaction.redact(text), f"key {R('github-token', SECRETS['github-token'])} and {R('secret', 'SECRET')}")
        self.assertEqual(redaction.redact(redaction.redact(text)), redaction.redact(text))

    def test_long_texts_are_labelled_in_windows_with_context(self):
        words = ["word"] * 9 + ["BSECRET", "ISECRET", "ESECRET"] + ["word"] * 9
        with patch.object(privacy_filter, "WINDOW", 4), patch.object(privacy_filter, "CONTEXT", 2):
            windowed = privacy_filter.spans(" ".join(words))
        self.assertEqual(self.session.windows, [6, 8, 8, 8, 7, 3])
        self.assertEqual(windowed, privacy_filter.spans(" ".join(words)))
        self.assertEqual(len(windowed), 1)

    def test_a_model_that_cannot_run_fails_the_write(self):
        with patch.object(privacy_filter, "_load", side_effect=OSError("download interrupted")):
            with self.assertRaisesRegex(RuntimeError, "redact.privacy_filter.*download interrupted.*Nothing was stored"):
                redaction.redact("anything")

    def test_decoder_only_allows_valid_spans(self):
        # Inside or end labels without a beginning become background or a span.
        scores = np.full((3, 33), -20.0)
        scores[0, 30], scores[1, 30], scores[2, 0] = 0.0, 0.0, -1.0
        path = privacy_filter._decode(scores)
        for previous, current in zip([0] + path, path + [0]):
            if previous and (previous - 1) % 4 in (0, 1):  # B or I continue the same category
                self.assertIn((current - 1) % 4, (1, 2))
                self.assertEqual((current - 1) // 4, (previous - 1) // 4)


# ── Text sent to language models ────────────────────────────────
class FakeModels:
    """Stand-ins for every provider SDK and the local model, recording the
    prompt each one receives."""
    REPLY = '{"type": "finding", "domains": ["deploy"], "topics": ["release"], "decisions": [], "bugs": [], ' \
            '"patterns": [], "facts": [], "summary": "ok"}'

    def __init__(self):
        self.received = []
        received, reply = self.received, self.REPLY

        class Messages:
            def create(self, **request):
                received.append(("anthropic", request["messages"][0]["content"]))
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=reply)])

        class Completions:
            def __init__(self, base_url):
                self.base_url = base_url

            def create(self, **request):
                received.append(("openai-compatible", request["messages"][0]["content"]))
                message = types.SimpleNamespace(content=reply)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        self.anthropic = types.SimpleNamespace(Anthropic=lambda **_: types.SimpleNamespace(messages=Messages()))
        self.openai = types.SimpleNamespace(OpenAI=lambda **kw: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=Completions(kw.get("base_url")))))

    def local_complete(self, llm, prompt, **_):
        self.received.append(("local", prompt))
        return self.REPLY


class ModelInputTests(unittest.TestCase):
    """What leaves Imprint for a model is redacted first, for every provider."""
    TEXT = f"We rotated the deploy key; the old one was {SECRETS['github-token']} and lives in CI."

    def setUp(self):
        self.models = FakeModels()
        llm = types.SimpleNamespace(tokenize=lambda data, add_bos=False: list(data), n_ctx=lambda: 10 ** 6,
                                    detokenize=bytes)
        for patcher in (patch.dict(sys.modules, {"anthropic": self.models.anthropic, "openai": self.models.openai}),
                        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test", "OPENAI_API_KEY": "test",
                                                "GOOGLE_API_KEY": "test", "IMPRINT_REDACT_PRIVACY_FILTER": "0"}),
                        patch("imprint.tagger._load_tagger_model", return_value=(llm, None)),
                        patch("imprint.local_llm.complete", side_effect=self.models.local_complete),
                        patch("imprint.taxonomy.get_all_values", return_value=[]),
                        patch("imprint.taxonomy.record_usage")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_redacted(self, expected_providers):
        self.assertEqual([provider for provider, _ in self.models.received], expected_providers)
        for _, prompt in self.models.received:
            self.assertNotIn(SECRETS['github-token'], prompt)
            self.assertIn(R('github-token', SECRETS['github-token']), prompt)

    def test_the_tagger_sends_every_provider_redacted_text(self):
        from imprint import tagger
        providers = ("anthropic", "openai", "gemini", "ollama", "vllm", "local")
        for provider in providers:
            with patch.object(tagger, "_get_llm_provider", return_value=provider):
                tags = tagger.build_payload_tags(self.TEXT, rel_path="notes.md", llm=True,
                                                 neighbor_context=f"before {SECRETS['github-token']}")
            self.assertEqual(tags["_llm_type"], "finding")
        self.assert_redacted(["anthropic"] + ["openai-compatible"] * 4 + ["local"])

    def test_the_summarizer_sends_every_provider_redacted_text(self):
        from imprint import cli_summarize
        settings = {"summarizer.model": "m", "summarizer.base_url": ""}
        for provider in ("local", "anthropic", "openai", "gemini", "ollama", "vllm"):
            with patch.object(cli_summarize.config_schema, "resolve",
                              side_effect=lambda key: ({**settings, "summarizer.provider": provider}.get(key, False), "")):
                cli_summarize._call_summarizer_llm(cli_summarize._SUMMARIZER_PROMPT + "USER:\n" + self.TEXT)
        self.assert_redacted(["local", "anthropic"] + ["openai-compatible"] * 4)

    def test_privacy_filter_redacts_model_input_too(self):
        from imprint import tagger
        with patch.object(privacy_filter, "_load", return_value=(FakeSession(), FakeTokenizer())), \
             patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "1"}), \
             patch.object(tagger, "_get_llm_provider", return_value="anthropic"):
            tagger.build_payload_tags("The vault word is SECRET.", llm=True)
        self.assertNotIn(" SECRET", self.models.received[0][1])
        self.assertIn(R("secret", "SECRET"), self.models.received[0][1])


@unittest.skipUnless(os.environ.get("IMPRINT_TEST_PRIVACY_FILTER_DATA"),
                     "set IMPRINT_TEST_PRIVACY_FILTER_DATA to a data dir that holds (or may download) the model")
class RealPrivacyFilterSmokeTests(unittest.TestCase):
    def test_the_pinned_model_finds_secrets_but_keeps_names(self):
        with patch.dict(os.environ, {"IMPRINT_DATA_DIR": os.environ["IMPRINT_TEST_PRIVACY_FILTER_DATA"],
                                     "IMPRINT_REDACT_PRIVACY_FILTER": "1"}):
            text = ("My name is Harry Potter, email harry.potter@hogwarts.edu. "
                    "The vault password is Tr0ub4dor&3 and my IBAN is DE89370400440532013000.")
            redacted = redaction.redact(text)
        self.assertIn("Harry Potter", redacted)
        self.assertIn("harry.potter@hogwarts.edu", redacted)
        self.assertNotIn("Tr0ub4dor&3", redacted)
        self.assertNotIn("DE89370400440532013000", redacted)
        self.assertEqual(redaction.redact(redacted), redacted)


# ── Every write path ────────────────────────────────────────────
class WritePathTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records

    def rows(self, workspace=None):
        client, coll = vs._ensure_collection(workspace)
        rows, _ = client.scroll(coll, with_payload=True, with_vectors=True, limit=100)
        return rows

    def contents(self, workspace=None):
        return sorted(row.payload["content"] for row in self.rows(workspace))

    def assert_clean(self, workspace=None):
        everything = json.dumps([row.payload for row in self.rows(workspace)])
        for secret in SECRETS.values():
            self.assertNotIn(secret, everything)

    def test_store_redacts_before_the_id_is_computed(self):
        mid = vs.store(f"Deploy with {SECRETS['github-token']}", project="p", source="note")
        self.assertEqual(self.contents(), [f"Deploy with {R('github-token', SECRETS['github-token'])}"])
        self.assertEqual(mid, vs._make_id(f"Deploy with {R('github-token', SECRETS['github-token'])}", "p", "note"))
        # The same note with another token of the same kind is the same memory.
        self.assertEqual(vs.store(f"Deploy with {'ghp_' + A[1:37]}", project="p", source="note"), mid)

    def test_replace_source_keeps_its_redacted_chunks(self):
        records = self.records([f"token {SECRETS['stripe-key']}", "plain chunk"])
        vs.replace_source(records, "project/app.py", "project")
        vs.replace_source(records, "project/app.py", "project")
        self.assertEqual(self.contents(), ["plain chunk", f"token {R('stripe-key', SECRETS['stripe-key'])}"])

    def test_embedding_sees_only_redacted_text(self):
        with patch("imprint.embeddings.embed_documents_batch",
                   side_effect=lambda texts: [[1., 0., 0.] for _ in texts]) as encode:
            vs.store_batch(self.records([f"key {SECRETS['aws-access-key']}"]))
        self.assertEqual(encode.call_args.args[0], [f"key {R('aws-access-key', SECRETS['aws-access-key'])}"])

    def test_ingest_of_a_file(self):
        from imprint import cli_index
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.py"
            path.write_text(f"STRIPE_KEY = '{SECRETS['stripe-key']}'\nDEBUG = False\n")
            settings = lambda key: (False, 'env') if key.startswith(('tagger.', 'redact.')) else (2_000_000, 'default')
            with patch('imprint.config_schema.resolve', side_effect=settings), \
                 patch('imprint.chunker.chunk_file', side_effect=lambda text, *args, **kwargs: [(text, 0)]), \
                 patch('imprint.tagger.build_payload_tags', return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                cli_index.ingest_single_file(str(path), 'fixture')
        self.assertEqual(self.contents(), [f"STRIPE_KEY = '{R('stripe-key', SECRETS['stripe-key'])}'\nDEBUG = False\n"])

    def test_learning_a_conversation(self):
        from imprint import cli_conversations
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "0f1e2d3c-session.jsonl"
            lines = [{"type": "user", "cwd": directory, "message": {"role": "user", "content":
                      f"Why does the deploy fail? I exported OPENAI_API_KEY={SECRETS['openai-key']} before running it."}},
                     {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text":
                      "The key is fine; the deploy fails because the region variable is missing from the job."}]}}]
            transcript.write_text("\n".join(json.dumps(line) for line in lines))
            with patch.object(cli_conversations, "owned", return_value=True), \
                 patch('imprint.chunker.chunk_prose', side_effect=lambda text: [text]), \
                 patch('imprint.tagger.build_payload_tags', return_value={}):
                stored, _ = cli_conversations.index_transcript(str(transcript))
        self.assertEqual(stored, 1)
        self.assertIn(f"OPENAI_API_KEY={R('openai-key', SECRETS['openai-key'])}", self.contents()[0])
        self.assert_clean()

    def test_mcp_store_and_ingest_content(self):
        from imprint import server
        with patch('imprint.tagger.build_payload_tags', return_value={}):
            self.assertTrue(server.store(f"The CI token is {SECRETS['gitlab-token']}, rotate it monthly.").startswith("Stored ["))
            self.assertTrue(server.ingest_content(f"# Setup\n\nexport HF_TOKEN={SECRETS['huggingface-token']}\n",
                                                  name="setup-notes").startswith("Stored 1 chunks"))
        contents = self.contents()
        self.assertIn(f"The CI token is {R('gitlab-token', SECRETS['gitlab-token'])}, rotate it monthly.", contents)
        self.assertTrue(any(f"export HF_TOKEN={R('huggingface-token', SECRETS['huggingface-token'])}" in text
                            for text in contents))
        self.assert_clean()

    def test_session_summaries_and_their_facts(self):
        from imprint import cli_summarize
        answer = json.dumps({"summary": f"Rotated the leaked key {SECRETS['anthropic-key']}.",
                             "decisions": [{"content": f"Use {SECRETS['sendgrid-key']} only in CI", "why": "scope"}],
                             "facts": [{"subject": "ci", "predicate": "uses", "object": SECRETS['npm-token']}]})
        self.addCleanup(kg._close_all)
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}), \
             patch.object(cli_summarize, "_extract_transcript_text", return_value="USER:\nx\n" * 6), \
             patch.object(cli_summarize, "_call_summarizer_llm", return_value=answer), \
             patch.object(config_schema, "resolve", side_effect=lambda key: (
                 {"summarizer.enabled": True, "summarizer.min_messages": 1}.get(key, False), "default")):
            result = cli_summarize.summarize_transcript(Path(directory) / "s.jsonl", project="p")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.contents(), [f"Rotated the leaked key {R('anthropic-key', SECRETS['anthropic-key'])}.",
                                           f"Use {R('sendgrid-key', SECRETS['sendgrid-key'])} only in CI\n\nWhy: scope"])
        self.assertEqual(kg.query()[0]["object"], R('npm-token', SECRETS['npm-token']))

    def test_facts_added_and_imported(self):
        self.addCleanup(kg._close_all)
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
            kg.add("deploy", "authenticates with", SECRETS['github-token'])
            record = {"subject": SECRETS['jwt'], "predicate": "is", "object": "a session", "valid_from": 5.0}
            self.assertTrue(kg.import_fact(record))
            self.assertFalse(kg.import_fact(record))  # deduplicated by its redacted text
            facts = {(fact["subject"], fact["object"]) for fact in kg.query(active_only=False)}
        self.assertEqual(facts, {("deploy", R('github-token', SECRETS['github-token'])), (R('jwt', SECRETS['jwt']), "a session")})

    def sync_buffer(self, records):
        lines = [json.dumps({'kind': 'meta', 'datasets': {'memories': len(records), 'facts': 0}, 'vectors': True}),
                 json.dumps({'kind': 'batch', 'dataset': 'memories', 'seq': 1, 'records': records}),
                 json.dumps({'kind': 'done'})]
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as buffer:
            buffer.write('\n'.join(lines) + '\n')
        self.addCleanup(os.unlink, buffer.name)
        return buffer.name

    def peer_record(self, content, vector, chunk_index=0):
        return {'id': vs._make_id(content, 'peer', 'peer/notes.md', chunk_index), 'content': content,
                'project': 'peer', 'source': 'peer/notes.md', 'chunk_index': chunk_index, 'vector': vector,
                'embedding_identity': config.embedding_identity(),
                'recorded_device': {'id': 'olderpeer1', 'name': 'old', 'os': 'linux'}, 'tagged_at': 7.0}

    def test_sync_import_redacts_what_an_older_peer_sent_and_embeds_it_again(self):
        secret = self.peer_record(f"Prod DB: postgres://app:{'S3cr3t!pass'}@db:5432/app", [0., 0., 1.])
        clean = self.peer_record("Nothing sensitive in this note at all.", [0., 1., 0.], chunk_index=1)
        with patch("imprint.embeddings.embed_documents_batch",
                   side_effect=lambda texts: [[1., 0., 0.] for _ in texts]) as encode, \
             patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
            stats = sync_ws.process_import_buffer(self.sync_buffer([secret, clean]), 'default')
        self.assertEqual(stats['memories'], {'inserted': 2, 'updated': 0, 'skipped': 0})
        expected = f"Prod DB: postgres://app:{R('password', 'S3cr3t!pass')}@db:5432/app"
        encode.assert_called_once_with([expected])
        rows = {row.payload['content']: row for row in self.rows()}
        redacted = rows[expected]
        self.assertEqual(redacted.payload['_mid'], vs._make_id(redacted.payload['content'], 'peer', 'peer/notes.md', 0))
        self.assertNotEqual(redacted.payload['_mid'], secret['id'])
        self.assertEqual(redacted.vector['dense'], [1., 0., 0.])  # not the sender's vector
        self.assertEqual((redacted.payload['tagged_at'], redacted.payload['recorded_device']['id']), (7.0, 'olderpeer1'))
        self.assertEqual(rows["Nothing sensitive in this note at all."].vector['dense'], [0., 1., 0.])
        self.assertEqual(rows["Nothing sensitive in this note at all."].payload['_mid'], clean['id'])
        # The peer sends its unredacted copy again: it is the memory already here.
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
            again = sync_ws.process_import_buffer(self.sync_buffer([secret]), 'default')
        self.assertEqual(again['memories'], {'inserted': 0, 'updated': 0, 'skipped': 1})
        self.assertNotIn("S3cr3t!pass", json.dumps([row.payload for row in self.rows()]))

    def sync(self, sender, receiver, privacy_filter_on):
        lines = sync_ws.stream_export_lines(sender)
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as buffer:
            buffer.write('\n'.join(lines) + '\n')
        self.addCleanup(os.unlink, buffer.name)
        with patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "1" if privacy_filter_on else "0"}):
            return sync_ws.process_import_buffer(buffer.name, receiver)['memories']

    def test_devices_with_different_settings(self):
        """Workspaces stand in for device A (Privacy Filter off) and B (on)."""
        self.addCleanup(kg._close_all)
        loaded = patch.object(privacy_filter, "_load", return_value=(FakeSession(), FakeTokenizer()))
        loaded.start()
        self.addCleanup(loaded.stop)
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': '', "IMPRINT_REDACT_PRIVACY_FILTER": "0"})
        env.start()
        self.addCleanup(env.stop)
        config.register_workspace('team')
        stricter = f"Ship the release with {R('secret', 'SECRET')} tomorrow"
        vs.store("Ship the release with SECRET tomorrow", project="p", source="note")
        self.assertEqual(self.sync('default', 'team', True), {'inserted': 1, 'updated': 0, 'skipped': 0})
        self.assertEqual(self.contents('team'), [stricter])
        # The stricter copy replaces the looser one on the looser device too.
        self.assertEqual(self.sync('team', 'default', False), {'inserted': 1, 'updated': 0, 'skipped': 0})
        self.assertEqual(self.contents(), [stricter])
        # Writing the looser text again on the looser device stores nothing new.
        vs.store("Ship the release with SECRET tomorrow", project="p", source="note")
        self.assertEqual(self.contents(), [stricter])
        self.assertEqual(self.sync('default', 'team', True), {'inserted': 0, 'updated': 0, 'skipped': 1})

    def test_a_tampered_id_is_still_refused_before_redaction(self):
        record = self.peer_record(f"key {SECRETS['google-api-key']}", [0., 0., 1.])
        record['id'] = '0' * 16
        with self.assertRaisesRegex(ValueError, "does not match"):
            vs.store_batch_precomputed([record])

    def test_a_failing_privacy_filter_stores_nothing(self):
        with patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "1"}), \
             patch.object(privacy_filter, "_load", side_effect=OSError("no model")):
            with self.assertRaisesRegex(RuntimeError, "no model"):
                vs.store("A note", project="p")
            with self.assertRaisesRegex(RuntimeError, "no model"):
                vs.replace_source(self.records(["chunk"]), "project/app.py", "project")
        self.assertEqual(self.contents(), [])


class PropagationTests(unittest.TestCase):
    """Workspaces default, team and third stand in for devices A, B and C
    that all held a memory and a fact from before redaction existed."""
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    contents = WritePathTests.contents
    rows = WritePathTests.rows
    sync = WritePathTests.sync

    LOOSE = f"Deploy with {SECRETS['github-token']} and the word SECRET"
    RULES = f"Deploy with {R('github-token', SECRETS['github-token'])} and the word SECRET"
    STRICT = f"Deploy with {R('github-token', SECRETS['github-token'])} and the word {R('secret', 'SECRET')}"

    def prepare(self):
        self.addCleanup(kg._close_all)
        loaded = patch.object(privacy_filter, "_load", return_value=(FakeSession(), FakeTokenizer()))
        loaded.start()
        self.addCleanup(loaded.stop)
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': '', "IMPRINT_REDACT_PRIVACY_FILTER": "0"})
        env.start()
        self.addCleanup(env.stop)
        for workspace in ('default', 'team', 'third'):
            config.register_workspace(workspace)
            client, coll = vs._ensure_collection(workspace)
            mid = vs._make_id(self.LOOSE, "p", "notes.md")
            client.upsert(coll, [models.PointStruct(id=vs._point_uuid(mid), vector={"dense": [0., 0., 1.]}, payload={
                "_mid": mid, "content": self.LOOSE, "project": "p", "source": "notes.md", "chunk_index": 0,
                "type": "decision", "tags": {"domain": ["infra"]}, "timestamp": 11.0, "tagged_at": 12.0,
                "recorded_device": {"id": "deviceone", "name": "one", "os": "linux"}, "recorded_at": 10.0})])
            with kg._connection(workspace) as conn, conn:
                conn.execute("INSERT INTO facts (subject, predicate, object, valid_from, source) VALUES (?, ?, ?, ?, ?)",
                             ("ci", "uses", SECRETS['npm-token'], 1.0, "notes.md"))

    def facts(self, workspace):
        return [(fact["subject"], fact["object"]) for fact in kg.query(active_only=False, workspace=workspace)]

    def test_one_scrub_reaches_every_device_and_the_strictest_redaction_wins(self):
        from imprint import cli_scrub
        self.prepare()
        clean_fact = [("ci", R('npm-token', SECRETS['npm-token']))]
        self.assertTrue(cli_scrub.scrub("default", apply=True)["applied"])  # only A scrubs
        self.assertEqual(self.contents(), [self.RULES])

        self.assertEqual(self.sync('default', 'team', False), {'inserted': 1, 'updated': 0, 'skipped': 0})
        self.assertEqual((self.contents('team'), self.facts('team')), ([self.RULES], clean_fact))
        # C, which never scrubbed, sends the old memory: it is not stored again.
        self.assertEqual(self.sync('third', 'team', False), {'inserted': 0, 'updated': 0, 'skipped': 1})
        self.assertEqual((self.contents('team'), self.facts('team')), ([self.RULES], clean_fact))
        # And it reaches C through B, without C scrubbing either.
        self.sync('team', 'third', False)
        self.assertEqual((self.contents('third'), self.facts('third')), ([self.RULES], clean_fact))

        # A turns Privacy Filter on and scrubs again: the further redaction
        # replaces the rules-only one on B and C, which keep it off.
        with patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "1"}):
            cli_scrub.scrub("default", apply=True)
        self.assertEqual(self.contents(), [self.STRICT])
        self.sync('default', 'team', False)
        self.assertEqual(self.contents('team'), [self.STRICT])
        self.assertEqual(self.sync('third', 'team', False), {'inserted': 0, 'updated': 0, 'skipped': 1})
        vs.store(self.LOOSE, project="p", source="notes.md", workspace="team")  # B writes the old text itself
        self.assertEqual(self.contents('team'), [self.STRICT])
        self.sync('team', 'third', False)
        self.assertEqual(self.contents('third'), [self.STRICT])
        stored = self.rows('third')[0].payload
        self.assertEqual((stored['recorded_at'], stored['tagged_at'], stored['recorded_device']['id']),
                         (10.0, 12.0, 'deviceone'))
        everything = json.dumps([row.payload for ws in ('default', 'team', 'third') for row in self.rows(ws)])
        self.assertNotIn(SECRETS['github-token'], everything)

    def test_a_looser_device_reindexing_keeps_the_stricter_chunk(self):
        self.prepare()
        client, coll = vs._ensure_collection('team')
        strict = f"token {R('stripe-key', SECRETS['stripe-key'])} and {R('secret', 'SECRET')}"
        mid = vs._make_id(strict, "project", "project/app.py", 0)
        client.upsert(coll, [models.PointStruct(id=vs._point_uuid(mid), vector={"dense": [1., 0., 0.]}, payload={
            "_mid": mid, "content": strict, "project": "project", "source": "project/app.py", "chunk_index": 0})])
        records = [{"content": f"token {SECRETS['stripe-key']} and SECRET", "project": "project",
                    "source": "project/app.py", "source_type": "file", "chunk_index": 0}]
        self.assertEqual(vs.replace_source(records, "project/app.py", "project", workspace="team"), (0, 1))
        self.assertIn(strict, self.contents('team'))
        self.assertNotIn(f"token {R('stripe-key', SECRETS['stripe-key'])} and SECRET", self.contents('team'))


class ScrubTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    rows = WritePathTests.rows

    def legacy(self, content, **payload):
        """A memory stored before redaction existed."""
        client, coll = vs._ensure_collection()
        mid = vs._make_id(content, "legacy", "legacy/notes.md", 0)
        client.upsert(coll, [models.PointStruct(id=vs._point_uuid(mid), vector={"dense": [0., 0., 1.]}, payload={
            "_mid": mid, "content": content, "project": "legacy", "source": "legacy/notes.md", "chunk_index": 0,
            "type": "decision", "tags": {"domain": ["infra"]}, "timestamp": 11.0, "tagged_at": 12.0,
            "recorded_device": {"id": "deviceone", "name": "one", "os": "linux"}, **payload})])
        return mid

    def test_dry_run_counts_and_apply_rewrites_in_place(self):
        from imprint import cli_scrub
        self.addCleanup(kg._close_all)
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        old = self.legacy(f"Use {SECRETS['github-token']} and {SECRETS['jwt']}")
        self.legacy("Nothing to see here")
        conn = kg._get_conn()
        conn.execute("INSERT INTO facts (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)",
                     ("ci", "uses", SECRETS['npm-token'], 1.0))
        conn.commit()

        preview = cli_scrub.scrub()
        self.assertEqual(preview, {"workspace": "default", "applied": False,
                                   "memories": {"scanned": 2, "redacted": 1}, "facts": {"scanned": 1, "redacted": 1},
                                   "kinds": {"github-token": 1, "jwt": 1, "npm-token": 1}})
        report = cli_scrub.summary(preview)
        self.assertIn("Dry run", report)
        for secret in SECRETS.values():
            self.assertNotIn(secret, report)
        self.assertIn(SECRETS['github-token'], json.dumps([row.payload for row in self.rows()]))

        with patch("imprint.embeddings.embed_documents_batch",
                   side_effect=lambda texts: [[1., 0., 0.] for _ in texts]) as encode:
            self.assertTrue(cli_scrub.scrub(apply=True)["applied"])
        expected = f"Use {R('github-token', SECRETS['github-token'])} and {R('jwt', SECRETS['jwt'])}"
        encode.assert_called_once_with([expected])
        rows = {row.payload["content"]: row for row in self.rows()}
        self.assertEqual(sorted(rows), ["Nothing to see here", expected])
        scrubbed = rows[expected]
        self.assertEqual(scrubbed.payload["_mid"], vs._make_id(scrubbed.payload["content"], "legacy", "legacy/notes.md", 0))
        self.assertNotEqual(scrubbed.payload["_mid"], old)
        self.assertEqual(scrubbed.vector["dense"], [1., 0., 0.])
        for key, value in {"project": "legacy", "source": "legacy/notes.md", "type": "decision", "timestamp": 11.0,
                           "tagged_at": 12.0, "tags": {"domain": ["infra"]}}.items():
            self.assertEqual(scrubbed.payload[key], value)
        self.assertEqual(scrubbed.payload["recorded_device"]["id"], "deviceone")
        self.assertEqual(kg.query()[0]["object"], R('npm-token', SECRETS['npm-token']))
        self.assertEqual(cli_scrub.scrub(apply=True)["memories"]["redacted"], 0)

    def test_snapshot_import_scrubs_what_it_restored(self):
        from imprint import cli_sync
        with tempfile.TemporaryDirectory() as bundle, \
             patch.object(self.client, "recover_snapshot", side_effect=lambda **_: self.legacy(f"k {SECRETS['npm-token']}")), \
             patch.object(self.client, "set_payload"), \
             patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}), contextlib.redirect_stderr(io.StringIO()):
            self.addCleanup(kg._close_all)
            Path(bundle, "manifest.json").write_text(json.dumps({"embedding_identity": config.embedding_identity()}))
            Path(bundle, "qdrant.snapshot").write_bytes(b"")
            cli_sync.import_snapshot(Path(bundle))
        self.assertEqual([row.payload["content"] for row in self.rows()], [f"k {R('npm-token', SECRETS['npm-token'])}"])


if __name__ == "__main__":
    unittest.main()
