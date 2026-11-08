---
title: ADR 0159 — A reply can ask the user and wait for the answer
description: Chat and Echo offer the model an ask_user tool; the reply pauses on a question that belongs to the API, waits outside its research time for an answer from any window or the next thing said, and goes on with its best assumption after 30 minutes.
---

- Status: Accepted
- Date: 2026-11-08
- Scope: the dashboard Chat page and Echo (Voice page and floating companion); local and remote chat models; real microphones and the quality of the questions models ask are unvalidated (see Validation)
- Amends:
  - [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) — time a reply spends waiting for the user's answer does not count against `chat.research_timeout_sec`; an answer counts as progress
  - [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md) — while Echo's reply waits for an answer, what the user says or types next is that answer, not a question that waits its turn
- Related: [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md), [ADR 0061](0061-local-tool-call-formats.md), [ADR 0146](0146-echo-saves-its-conversations-in-chat-history-and-carries-chats-on.md), [ADR 0158](0158-reminders-timers-and-scheduled-jobs-fire-from-the-api-process.md), [dashboard](../dashboard.md), [live voice](../live-voice.md), [API](../api.md)

## Context

The user asked that chat and voice behave like an agentic harness such as Claude Code or opencode: when a request is ambiguous, Imprint should ask a clarifying question in the middle of the reply and carry on the same reply with the answer. Examples given: narrowing a search ("which project?", "this week or last month?") and filling in a flow ("what time should I remind you?", with reminders from ADR 0158).

Before this record a reply could only answer or guess. The tool loop in `stream_reply` (`imprint/chat.py`) ran every call of a model turn and went on; nothing could wait for the user. Three existing rules shaped the design:

- **The research time budget is a deadline** (`ResearchBudget` in `imprint/chat_research.py`, ADR 0102). A reply that waited for a person against it would end research, or reach the reserved answer, while the user was still reading the question.
- **A reply belongs to the API, not to its connection** (ADR 0145). A Chat reply runs on its own thread in `imprint/chat_replies.py`, and watchers replay its events. A question shown only to the window that asked would be lost when that window closes.
- **Speech during an Echo reply is the next question** (ADR 0145). It waits in the conversation's queue. An answer to Echo's question has to go to the waiting reply instead.

## Decision

**The model can ask.** Chat and Echo offer an `ask_user` tool with `question` (required), `options` (2–5 short choices) and `allow_other` (default true). The strategy guide gets one line: ask only when the answer changes what you do and search cannot settle it, never ask more than needed, and offer options. The tool is offered only when the caller can deliver an answer. The dashboard's chat API and Echo can. Other callers, and models without a tool format, do not get it. It writes nothing, so voice's read-only mode offers it.

**The tool loop runs it.** `stream_reply` takes an `ask` callable. On an `ask_user` call it:

1. yields `{"type": "question", "id", "question", "options", "allow_other"}`;
2. waits on what `ask` returned, a condition that the answer, the user's stop or the end of the reply wakes, with no polling;
3. moves the research deadline by the time it waited;
4. yields `{"type": "answer", "id", "text"}`;
5. hands the model `The user answered: …` as the tool result.

Other calls in the same model turn run as usual. Fewer than two options are none, so the user answers in their own words. Options a local model wrote as lines or a comma list become a list. A JSON list in Qwen XML markup is typed by the schema like every other argument. An answer counts as research progress, as a write does.

**Nobody answering is not a failure.** After `ANSWER_TIMEOUT`, 30 minutes, the model reads that no answer came and is asked to go on with its best assumption and say what it assumed. The user's stop ends the wait at once and stops the reply.

**The question belongs to the reply in the API.** The question is one of the reply's events, so a Chat window that comes back replays it, unanswered, and can answer it. `GET /api/chat/sessions/{sid}` also reports the waiting `question`. `POST /api/chat/sessions/{sid}/answer` with `{id, answer}` answers it from any window:

- 404 when no reply is being written;
- 409 when no question with that id waits;
- 400 for an empty answer, one over 2,000 characters, or a choice outside the options when `allow_other` is false.

**History keeps the exchange.** The question and its answer are stored as the reply's `ask_user` tool exchange: the arguments hold the question and options, and the result holds the answer. Later turns therefore see what was asked and answered, and Chat history shows the question with its answer.

**Chat shows a card.** The question appears in the reply as a card with a button per option and, when allowed, a box for an answer in the user's own words. The card takes the focus, and Enter sends the answer. While the question waits, the message box answers it too. An answered card is disabled and shows the answer; one left without an answer says so.

**Echo asks aloud.** Echo shows the question with its choices to tap and speaks it with them ("You can say Imprint or Billing, or something else."). While the question waits:

- a tap, the next typed message or the next utterance is the answer, not a queued question, and it shows on the question card, not as a turn;
- noise and Echo hearing itself, the question included, are still ignored;
- a spoken "stop" still stops the reply, and Stop reply ends the wait.

The voice turn's inactivity timeout is extended by the answer wait while Echo waits. The WebSocket protocol grows a client `answer` control (`question_id`, `text`) and the server events `question`, `answering` (an utterance was taken as the answer) and `answered`. A turn's snapshot carries its `question` for a window that comes back.

## Rejected alternatives

- **Ending the reply with a question and treating the user's next message as a new turn.** It needs no new machinery. But the research done so far is lost to the new turn except as history, a flow such as setting a reminder starts over, and the user explicitly asked to continue the same reply.
- **Waiting against the research deadline.** A person reading and deciding routinely takes longer than the 300-second default. The reply would then go to the reserved answer without the answer it asked for.
- **Keeping the question only in the connection that saw it.** It contradicts ADR 0145: closing the tab or opening another chat would leave the reply waiting for an answer no window can give, until the timeout.
- **A separate question queue or WebSocket for Chat.** Replies already replay their events to every watcher. The question as an event, plus one POST route, needs no new channel.
- **Polling the answer and the stop in short sleeps.** It works, but every waiting reply wakes several times a second for up to 30 minutes. A condition that the answer and the stop both notify costs nothing while waiting.

## Consequences

- **Agentic flows.** A model can narrow a search or complete a scheduling flow in one reply. How well it chooses when to ask depends on the model and is unmeasured.
- **Longer replies, not blocked chats.** A reply can take up to 30 minutes plus its research time. While it waits on the user it gives up ADR 0145's writing slot, since it is not using the model, so replies in other chats are written meanwhile; after the answer it takes the slot back, waiting first for a reply another chat is writing.
- **Remote prompt caches.** A provider's prompt cache may expire during a long wait, so the next request can cost more than the prefix-aware charging assumes.
- **Answers are kept.** Answers are stored in Chat history like any message; voice saves them unless saving is off (ADR 0146). Chat history is not redacted on write, as before.
- **Older clients.** A dashboard page from before this change shows neither the question nor a way to answer it. Its reply then goes on after 30 minutes with the model's assumption.
- **API restarts.** An API restart loses a waiting question with its reply, as ADR 0145 already notes for replies.

## Invalidation conditions

- If models ask when they should search, or ask repeatedly within one reply, then limit questions per reply or tighten the strategy line, measured on stored replies.
- If unanswered questions pile up across chats, then shorten the wait or limit how many chats can wait at once.
- If voice takes background speech as the answer in real rooms, then confirm the heard answer before giving it to the reply.
- If replies must survive an API restart, then the waiting question must be stored with the reply's progress (ADR 0145's condition).

## Validation

Executed on Linux (WSL2) in this working tree:

- `tests/test_chat_ask_user.py`, 17 tests:
  - a remote reply pauses on the question, resumes with a chosen option and with a free-text answer, and runs the batch's other calls;
  - waiting an hour against a 300-second budget leaves research running;
  - nobody answering gives the best-assumption result;
  - the user's stop ends the wait at once;
  - voice offers `ask_user` but not `store`;
  - without `ask` the tool is not offered;
  - bad questions and options are reported or normalized;
  - Qwen XML markup with a JSON list, or with lines, gives a list of options;
  - a reply waiting on the user gives up the writing slot: another chat's reply is written meanwhile, and the first takes the slot back after the answer (`test_chat_replies`);
  - through the API, a window that comes back sees the question in `GET /api/chat/sessions/{sid}` and in `/reply`, answers it, and the stored chat holds the `ask_user` exchange; answers are validated; Stop while a question waits ends the reply;
  - Echo speaks the question with its choices, takes the next utterance as the answer without queueing it, takes a tapped choice or typed text, and Stop reply closes the wait.
- With the existing chat and voice suites (`test_chat_stream`, `test_chat_analysis`, `test_chat_research`, `test_chat_tool_coverage`, `test_chat_replies`, `test_chat_memory`, `test_voice`), whose stand-ins now take the `ask` argument: 161 tests pass.
- `imprint/ui`: `tsc --noEmit` and ESLint on the changed files are clean. Playwright ran against the static build in headless Chromium:
  - `tests/chat-questions.spec.ts`, 7 tests, pass: the card takes the focus and an option answers; Enter sends the user's own words; the message box answers; a chat opened again answers the waiting question; Chat history shows a stored answer; the card is legible in the light and dark themes;
  - the two new cases in `tests/voice.spec.ts` (a tapped choice; a typed answer shown on the question, not as a turn) pass;
  - `tests/chat.spec.ts` and the existing voice cases pass. With several workers on this WSL machine, microphone and WebGL cases timed out, a different set in each run; each failure passed in a later run with one worker. No baseline run without this change was made.
- Not verified: real local and remote models deciding when to ask; a spoken answer through a real microphone and room; macOS and Windows; screen readers announcing the card.
