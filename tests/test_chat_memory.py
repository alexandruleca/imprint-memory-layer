"""Conversation memory (ADR 0141): the conversation_history tool, the running
summary that stands in for exchanges that no longer fit, its persistence with
a chat session, and voice's in-memory copy."""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from imprint import chat, chat_memory, chat_store
from test_chat_stream import LocalModel, llama_binding, remote_provider, text_chunk


def compose_file(services=40):
    """A pasted compose file whose billing service sits deep inside."""
    blocks = [f"  service-{n}:\n    image: registry.internal/acme/service-{n}:1.{n}.0\n    restart: unless-stopped\n"
              f"    environment:\n      LOG_LEVEL: info\n      SERVICE_{n}_PORT: \"{9000 + n}\"\n    replicas: {n % 3 + 1}\n"
              for n in range(services)]
    blocks.insert(services * 2 // 3, "  billing:\n    image: registry.internal/acme/billing:2.3.4\n"
                                     "    environment:\n      BILLING_PORT: \"8101\"\n      LOG_LEVEL: info\n")
    return "Here is our staging compose file, keep it in mind:\n\nservices:\n" + "".join(blocks)


def numbered(*pairs):
    return [{"seq": n, "role": role, "content": content} for n, (role, content) in enumerate(pairs, 1)]


def tokens(llm, items, tools, remote):
    """Three characters a token, as remote prompts are estimated."""
    return (len(json.dumps({"messages": items, "tools": tools}, ensure_ascii=False)) + 2) // 3


class HistoryToolTests(unittest.TestCase):
    def test_a_long_paste_is_found_deep_inside_and_read_exactly_by_range(self):
        paste = compose_file()
        history = chat_memory.History(numbered(("user", paste), ("assistant", "Got it."),
                                               ("user", "what about billing?"), ("assistant", "It has its own image.")))
        found = history.run(query="billing port")
        self.assertIn('BILLING_PORT: "8101"', found)
        self.assertTrue(found.splitlines()[2].startswith("#1 user · "), found)
        place = next(part for part in found.split("[characters ")[1:] if "BILLING_PORT" in part)
        start = int(place.split("–")[0].replace(",", ""))
        self.assertIn('BILLING_PORT: "8101"', paste[start:start + chat_memory.SNIPPET + 50])
        read = history.run(message=1, start=start)
        self.assertEqual(read.split("\n", 1)[1].split("\nnext_start=")[0], paste[start:start + chat_memory.READ_CHARS])
        self.assertIn('BILLING_PORT: "8101"', read)
        # A long message continues where a read stopped, to its end.
        pages, offset = [], 0
        while True:
            page = history.read(1, offset)
            body, _, rest = page.split("\n", 1)[1].partition("\nnext_start=")
            pages.append(body)
            if not rest:
                break
            offset = int(rest)
        self.assertEqual("".join(pages), paste)

    def test_results_are_bounded_numbered_by_seq_and_scoped_to_the_messages_given(self):
        long = "alpha beta gamma " * 2000
        messages = [{"seq": 10 + n, "role": "user" if n % 2 == 0 else "assistant", "content": long}
                    for n in range(40)]
        history = chat_memory.History(messages)
        found = history.run(workspace="someone-else", query="alpha gamma")
        self.assertLessEqual(len(found), chat_memory.READ_CHARS + 100)
        self.assertIn("#49 assistant", found)  # newest first among equals
        self.assertLessEqual(len(history.read(10)), chat_memory.READ_CHARS + 200)
        self.assertLessEqual(len(history.overview()), chat_memory.OVERVIEW_CHARS)
        self.assertIn("No message #3", history.read(3))
        self.assertIn("Earlier messages are #10–#49", history.read(3))
        self.assertIn("[tool error]", history.read(10, start=len(long)))
        # Nothing outside the conversation handed in is searchable.
        other = chat_memory.History(numbered(("user", "the billing port is 8101")))
        self.assertIn("No earlier message", history.run(query="billing port"))
        self.assertIn("8101", other.run(query="billing port"))

    def test_tool_results_are_searchable_by_what_was_asked(self):
        history = chat_memory.History([
            {"seq": 1, "role": "user", "content": "what gpu does bedrock have?"},
            {"seq": 2, "role": "tool", "content": "[1] bedrock has an RTX 5070 Ti with 16 GB", "tool_name": "search",
             "tool_args": {"query": "bedrock gpu"}},
            {"seq": 3, "role": "assistant", "content": "An RTX 5070 Ti."}])
        found = history.run(query="5070 vram bedrock")
        self.assertIn("#2 tool search “bedrock gpu”", found)
        self.assertIn("#2 tool search “bedrock gpu”", history.overview())

    def test_without_words_to_find_it_lists_the_conversation(self):
        history = chat_memory.History(numbered(("user", "hello"), ("assistant", "Hi.")))
        self.assertIn("#1 user: hello", history.run())
        self.assertIn("#2 assistant: Hi.", history.run(query="what did i say earlier?"))
        self.assertIn("No earlier message in this conversation matches", history.run(query="kubernetes"))


class SummaryTests(unittest.TestCase):
    def test_plan_covers_nothing_while_the_text_fits_then_the_oldest_exchanges_with_headroom(self):
        earlier = numbered(*[(role, f"{role} {n} " + "x" * 300) for n in range(10) for role in ("user", "assistant")])
        size = lambda messages: sum(len(m["content"]) for m in messages) // 3

        def spare(history, summarized):
            return limit - size(history) - (chat_memory.SUMMARY_RESERVE // 2 if summarized else 0)

        limit = 10_000
        self.assertEqual(chat_memory.plan(earlier, 0, spare), [])
        limit = 2_000
        covered = chat_memory.plan(earlier, 0, spare)
        kept = [m for m in earlier if m["seq"] > covered[-1]["seq"]]
        self.assertEqual(covered[0]["seq"], 1)
        self.assertEqual(kept[0]["role"], "user")  # an exchange boundary
        headroom = (limit - chat_memory.SUMMARY_RESERVE) // 2
        self.assertGreaterEqual(spare(kept, False), chat_memory.SUMMARY_RESERVE + headroom)
        # Already covered messages are never covered again.
        self.assertTrue(all(m["seq"] > 4 for m in chat_memory.plan(earlier, 4, spare)))

    def test_request_clips_long_messages_names_tool_results_and_fits(self):
        covered = [{"seq": 1, "role": "user", "content": compose_file()},
                   {"seq": 2, "role": "tool", "content": "x" * 5000, "tool_name": "search", "tool_args": {"query": "q"}},
                   {"seq": 3, "role": "assistant", "content": "Got it."}]
        items = chat_memory.request("- Earlier point (#0).", covered, lambda items: len(items[1]["content"]) < 900)
        body = items[1]["content"]
        self.assertEqual(items[0]["content"], chat_memory.SUMMARY_SYSTEM)
        self.assertIn("Summary so far:\n- Earlier point (#0).", body)
        self.assertIn("characters omitted", body)
        self.assertIn("[#2 tool search “q”: 5,000 characters of results, not shown]", body)
        self.assertTrue(body.endswith("one for each exchange from #1 to #3."))
        with self.assertRaises(ValueError):
            chat_memory.request("", covered, lambda items: False)

    def test_new_points_are_appended_and_the_oldest_go_first_beyond_the_cap(self):
        self.assertEqual(chat_memory.clean("<think>x</think>\n- One (#1).\n- Two, cut sho"), "- One (#1).")
        previous = "\n".join(f"- Point {n} " + "p" * 80 + f" (#{n})." for n in range(30))
        merged = chat_memory.merge(previous, "- Newest (#40).")
        self.assertLessEqual(len(merged), chat_memory.SUMMARY_CHARS)
        self.assertTrue(merged.endswith("- Newest (#40)."))
        self.assertNotIn("Point 0 ", merged)


class LocalSummaryReplyTests(unittest.TestCase):
    """stream_reply with a local model whose prompts are measured at three
    characters a token, in a temporary data directory."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory.name, "IMPRINT_CHAT_TOOL_FORMAT": "qwen-xml",
                                                "IMPRINT_CHAT_MAX_TOKENS": "512"}),
                        patch.object(chat, "_prompt_tokens", side_effect=tokens)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reply(self, model, prior, question, summary=None, cancel=None):
        with patch.object(chat, "_is_remote_provider", return_value=False), \
             patch.object(chat, "_llama", return_value=object()), \
             patch.object(chat, "_model_missing", return_value=False), \
             patch.object(chat, "_llm", model), \
             patch.object(chat, "load_model", return_value=(model, None)), llama_binding():
            return list(chat.stream_reply(prior, question, summary=summary, cancel_event=cancel))

    def conversation(self):
        """A paste, then enough later talk that the text no longer fits."""
        prior = [("user", compose_file()), ("assistant", "Got it: the staging stack.")]
        for n in range(6):
            prior += [("user", f"question {n} about the rollout plan"), ("assistant", f"Answer {n}: " + "detail " * 260)]
        return numbered(*prior)

    def test_a_pasted_message_compacted_out_of_the_prompt_is_found_and_quoted(self):
        call = ("<tool_call>\n<function=conversation_history>\n<parameter=query>\nbilling port\n</parameter>\n"
                "</function>\n</tool_call>")
        model = LocalModel(["- Pasted the staging compose file (#1–#2).\n- Asked about the rollout plan (#3–#4)."],
                           [call], ["Billing listens on port 8101, from the compose file you pasted (#1)."])
        events = self.reply(model, self.conversation(), "Which port does billing use in the compose file I pasted?")
        kinds = [event["type"] for event in events]
        self.assertIn({"type": "analysis", "text": "Summarizing the earlier conversation…"}, events)
        summary = next(event for event in events if event["type"] == "summary")
        self.assertGreaterEqual(summary["covers"], 2)
        self.assertEqual(summary["retry_after"], 0)
        self.assertTrue(summary["text"].startswith("- Pasted the staging compose file (#1–#2)."))
        self.assertLess(kinds.index("summary"), kinds.index("tool_call"))
        # The summarizer read the paste clipped, with its message number.
        self.assertIn("[#1 user]", model.requests[0]["messages"][1]["content"])
        self.assertIn("characters omitted", model.requests[0]["messages"][1]["content"])
        # The reply's prompt holds the summary in place of the paste, and the tool.
        first = model.requests[1]
        system = first["messages"][0]["content"]
        self.assertIn("# Earlier in this conversation", system)
        self.assertIn(f"Messages #1–#{summary['covers']} are summarized here instead of shown.", system)
        self.assertIn("#1 the user pasted", system)
        self.assertIn(chat_memory.STRATEGY.strip(), system)
        self.assertNotIn("BILLING_PORT", json.dumps(first["messages"]))
        self.assertIn(chat_memory.TOOL, [tool["function"]["name"] for tool in first["tools"]])
        self.assertIn({"type": "tool_call", "name": chat_memory.TOOL, "args": {"query": "billing port"}}, events)
        result = next(event["result"] for event in events if event["type"] == "tool_result")
        self.assertIn('BILLING_PORT: "8101"', result)
        self.assertIn("#1 user", result)
        self.assertEqual(events[-2], {"type": "assistant_message",
                                      "text": "Billing listens on port 8101, from the compose file you pasted (#1)."})

    def test_a_stored_summary_stands_in_for_what_it_covers_and_no_new_one_runs_while_the_rest_fits(self):
        prior = numbered(("user", "secret plan alpha"), ("assistant", "Noted alpha."), ("user", "and beta"),
                         ("assistant", "Noted beta."))
        model = LocalModel(["Beta is next."])
        events = self.reply(model, prior, "what next?", summary={"text": "- Plan alpha noted (#1–#2).", "covers": 2})
        self.assertNotIn("summary", [event["type"] for event in events])
        messages = model.requests[0]["messages"]
        self.assertIn("- Plan alpha noted (#1–#2).", messages[0]["content"])
        self.assertEqual([m["content"] for m in messages[1:]], ["and beta", "Noted beta.", "what next?"])

    def test_a_summary_that_runs_out_of_time_falls_back_to_dropping_and_waits_before_trying_again(self):
        prior = self.conversation()
        model = LocalModel(["The rollout is staged."], ["Still staged."])
        with patch.object(chat_memory, "SUMMARY_TIMEOUT", 0):
            events = self.reply(model, prior, "and the rollout?")
        summary = next(event for event in events if event["type"] == "summary")
        self.assertEqual((summary["text"], summary["covers"]), ("", 0))
        self.assertEqual(summary["retry_after"], prior[-1]["seq"] + chat_memory.RETRY_MESSAGES)
        self.assertEqual(events[-2], {"type": "assistant_message", "text": "The rollout is staged."})
        # ADR 0140's dropping fitted the prompt: the oldest exchanges went, whole.
        messages = model.requests[0]["messages"]
        self.assertEqual(messages[1]["role"], "user")
        self.assertNotIn("BILLING_PORT", json.dumps(messages))
        self.assertLessEqual(tokens(None, messages, model.requests[0]["tools"], False) + 512 + 16, 8192)
        # The next reply, two messages later, does not try again yet.
        later = prior + numbered(*[("user", "x")] * (len(prior) + 2))[len(prior):]
        events = self.reply(model, later, "and now?", summary=summary)
        self.assertNotIn("analysis", [event["type"] for event in events])
        self.assertNotIn("summary", [event["type"] for event in events])

    def test_secrets_are_redacted_before_the_summarizer_reads_them_and_in_what_it_writes(self):
        key = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
        prior = self.conversation()
        prior[2]["content"] = f"my token is {key}, " + prior[2]["content"]
        model = LocalModel([f"- Shared token {key} (#3)."], ["Done."])
        events = self.reply(model, prior, "ok?")
        self.assertNotIn(key, json.dumps(model.requests[0]))
        summary = next(event for event in events if event["type"] == "summary")
        self.assertNotIn(key, summary["text"])
        self.assertIn("[REDACTED:", summary["text"])

    def test_cancelling_while_summarizing_ends_the_reply_without_a_summary(self):
        cancel = threading.Event()
        model = LocalModel(["- one (#1).", " never"])
        original = model.generate

        def generate(tokens, **sampling):
            for token in original(tokens, **sampling):
                cancel.set()
                yield token
        model.generate = generate
        events = self.reply(model, self.conversation(), "and?", cancel=cancel)
        self.assertNotIn("summary", [event["type"] for event in events])
        self.assertNotIn("assistant_message", [event["type"] for event in events])

    def test_the_final_answer_keeps_the_summary_but_not_the_lookup_guidance(self):
        messages = [{"role": "system", "content": "base" + chat.TOOL_STRATEGY + chat_memory.STRATEGY
                     + "\n\n# Earlier in this conversation\nMessages #1–#2 are summarized here instead of shown."
                     + chat_memory.LOOKUP + "\n\n- point (#1)."},
                    {"role": "user", "content": "question"}]
        system = chat._final_messages(messages, [], "Time is up.", spoken=False)[0]["content"]
        self.assertIn("- point (#1).", system)
        for guidance in (chat.TOOL_STRATEGY, chat_memory.STRATEGY, chat_memory.LOOKUP):
            self.assertNotIn(guidance, system)


class RemoteHistoryToolTests(unittest.TestCase):
    def test_the_tool_is_offered_only_with_earlier_messages_and_voice_keeps_it(self):
        client, provider = remote_provider([text_chunk("Hi.")], [text_chunk("Hi again.")])
        with provider[0], provider[1], provider[2]:
            list(chat.stream_reply([], "hello", read_only=True))
            list(chat.stream_reply(numbered(("user", "hello"), ("assistant", "Hi.")), "again", read_only=True))
        first, second = (call.kwargs for call in client.chat.completions.create.call_args_list)
        self.assertNotIn(chat_memory.TOOL, [tool["function"]["name"] for tool in first["tools"]])
        self.assertNotIn(chat_memory.STRATEGY, first["messages"][0]["content"])
        self.assertIn(chat_memory.TOOL, [tool["function"]["name"] for tool in second["tools"]])
        self.assertIn(chat_memory.STRATEGY, second["messages"][0]["content"])


class SessionSummaryStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory.name}),
                        patch.dict(chat_store._conns, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(chat_store._close_all)

    def test_the_summary_is_kept_with_the_session_and_goes_with_it(self):
        sid = chat_store.create_session()
        for role, content in [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d"), ("user", "e")]:
            chat_store.append_message(sid, role, content)
        self.assertIsNone(chat_store.get_summary(sid))
        chat_store.set_summary(sid, "- a and b (#1–#2).", 2)
        chat_store.set_summary(sid, "- a to d (#1–#4).", 4, retry_after=0)
        summary = chat_store.get_summary(sid)
        self.assertEqual((summary["text"], summary["covers"], summary["exchanges"]), ("- a to d (#1–#4).", 4, 2))
        chat_store.set_summary("missing", "x", 1)
        self.assertIsNone(chat_store.get_summary("missing"))
        chat_store.delete_session(sid)
        self.assertIsNone(chat_store.get_summary(sid))

    def test_the_chat_route_passes_the_summary_in_and_records_the_new_one(self):
        from imprint import api
        sid = chat_store.create_session()
        chat_store.append_message(sid, "user", "first")
        chat_store.set_summary(sid, "- old (#1).", 1)
        seen = {}

        def reply(prior, message, workspace="", summary=None):
            seen["summary"] = summary
            yield {"type": "summary", "text": "- new (#1–#2).", "covers": 2, "retry_after": 0, "exchanges": 1}
            yield {"type": "assistant_message", "text": "ok"}
            yield {"type": "done"}

        with patch.object(api.chat_mod, "stream_reply", side_effect=reply):
            list(api._chat_events(sid, chat_store.get_messages(sid), "second", "default", chat_store.get_summary(sid)))
        self.assertEqual(seen["summary"]["text"], "- old (#1).")
        self.assertEqual(chat_store.get_summary(sid)["text"], "- new (#1–#2).")
        self.assertEqual(api.api_chat_get_session(sid)["summary"]["covers"], 2)


if __name__ == "__main__":
    unittest.main()
