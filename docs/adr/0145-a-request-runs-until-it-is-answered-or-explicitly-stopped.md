---
title: ADR 0145 — A request runs until it is answered or explicitly stopped
description: Echo's voice conversation and each Chat reply belong to the API instead of the connection that asked, so switching modes, speaking, hiding or leaving never cancels them; a window that comes back picks the reply up, and only the user's stop cancels.
---

- Status: Accepted
- Date: 2026-11-06
- Scope: Echo (Voice page and floating companion) and the dashboard Chat page; real microphones, rooms and speakers are unvalidated (see Validation)
- Amends:
  - [ADR 0043](0043-live-voice-and-echo-companion.md) — speech onset, push-to-talk and a new question no longer flush playback and cancel the reply; only fully played replies no longer gate history; hidden pages release capture but no longer end the conversation
  - [ADR 0071](0071-each-look-speaks-in-its-own-voice.md) — a new look or reasoning model during a reply takes over once that reply is answered, instead of ending it
  - [ADR 0092](0092-voice-generation-follows-playback.md) — the `played` acknowledgement no longer commits history; playback credit is released for good when the window playing it goes away
  - [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) — folded, Echo's page releases the microphone and lets go of the conversation, which goes on in the API, instead of stopping it
  - [ADR 0141](0141-chat-summarizes-what-no-longer-fits-and-can-look-back.md) — voice keeps every completed reply in its in-memory history, played to the end or not
- Related: [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md), [ADR 0137](0137-echo-wakes-on-hey-echo-from-a-hidden-desktop-listener.md), [live voice](../live-voice.md), [dashboard](../dashboard.md)

## Context

The user reported: "Switching modes cancels the current operation and it never resumes. Sometimes speaking also does the same and it doesn't continue. It should always try to complete the request, so an accidental speech doesn't break the process of generating the data."

Every path that ended a reply was a deliberate design from ADR 0043, which made voice interruptible on purpose. In the code before this record (commit `35ea9ae`):

- **Switching modes cancelled.** The Voice page and the floating Echo offer **Push to talk** and **Hands-free**. `chooseMode` called `VoiceClient.setMode` (`imprint/ui/src/components/voice-surface.tsx:196`), whose first statement was `this.interrupt()` (`imprint/ui/src/lib/voice-client.ts:375-376`). `interrupt()` stopped playback and sent `{"type": "cancel"}` whenever a turn was pending (`voice-client.ts:418-424`). The API answered a cancel by cancelling the turn task and killing the native speech worker (`imprint/voice_api.py:587`, `cancel(force=True)`). Changing the reasoning model or Echo's look restarted the session through `start()`, which began with `stop()` (`voice-surface.tsx:191,199`, `voice-client.ts:228`).
- **Speaking cancelled.** In hands-free, the energy gate's onset called `interrupt()` (`voice-client.ts:401`); a cough, a knock or Echo's own voice through the speakers was enough. Holding push-to-talk did the same (`voice-client.ts:382`), and so did sending any new question (`voice-client.ts:414`). The API also cancelled the running turn whenever a new `turn` arrived (`voice_api.py:611`). A spoken noise that transcribed to nothing then ended in "I didn't catch a short question", so the original question was lost and nothing was asked instead.
- **Hiding, folding, floating and closing cancelled.** A hidden page called `stop()` (`voice-surface.tsx:118`), as did unmounting (`:124`), the dashboard handing voice to the floating Echo (`:120`, `:228`), folding Echo into the orb (`:294`; the desktop app hides the window, `ui-electron/main.js:838`) and Close (`:288`). `stop()` closed the WebSocket, and the API's `finally` cancelled the turn and stopped the worker (`voice_api.py:629`). The conversation and its history lived in the socket handler, so nothing could be picked up again.
- **Nothing resumed.** History only took a reply after the client's `played` acknowledgement (`voice_api.py:563,590`), so a cancelled or unheard reply left no trace.
- **Chat had the same shape.** `/api/chat` generated the reply inside the `StreamingResponse` that the request watched (`imprint/api.py:829`); when the connection closed, Starlette closed the generator and generation stopped, keeping only the partial text (`api.py:837`). The Chat page aborted that request when another chat was opened or a new chat started (`imprint/ui/src/app/chat/page.tsx:105,127`), so switching chats stopped the reply.

## Decision

**A request belongs to the API, not to the connection that asked.** Only the user's explicit stop cancels it.

**Echo's conversation outlives its window.** The API keeps one voice conversation (`Conversation` in `imprint/voice_api.py`): its in-memory history and summary (ADR 0141), its recent turns and a queue. A window attaches to it over `WS /api/voice/stream`; closing, hiding, folding, floating or reloading the window only detaches it. A reply that loses its window goes on: its text is recorded, its audio is skipped, and playback credit stops being awaited. While a window is attached or a question runs, the worker is held; otherwise it is stopped as before. The conversation is forgotten 30 minutes after its window left with nothing running, when the user ends it, or when the API stops; it is never written to disk.

**A window that comes back picks it up.** `GET /api/voice/conversation` reports the recent turns, whether a reply runs and whether a window holds the conversation, without starting the worker. A Voice page or floating Echo that mounts or becomes visible reconnects at once when a reply is running or waiting, and shows a reply that finished meanwhile, marked "Answered while Echo was away", with **Play answer**; otherwise it offers **Continue conversation**. Reconnecting never turns the microphone on, whatever the remembered choice (ADR 0069). The `ready` event carries the turns and the last turn id so the window shows progress and keeps numbering. A different look, model or workspace takes over once the reply in progress is answered; an idle conversation gives way at once (ADR 0071).

**Speech during a reply is the next question.** Switching between push to talk and hands-free never touches the reply. A question typed or spoken while Echo answers is sent at once and waits its turn in the API (`queued`, then `start`), up to four; the window shows it as waiting with **Remove**. A spoken question is transcribed as soon as the speech worker is free between sentences (one worker request at a time), then:

- an utterance with fewer than 12 voiced 20 ms frames never leaves the page;
- one that transcribes to nothing, fillers or sound labels ("um", "[cough]") is `ignored`;
- one that began while Echo's voice played (`overlap`) and is mostly one run of a recent answer (at least 3 words, 80 % contiguous) is Echo hearing itself and is `ignored`;
- "stop", "cancel", "never mind" and the like, said alone, stop the reply and every waiting question (`cancelled`, then `stopped`); in a wake conversation a goodbye ends it after the reply in progress (ADR 0137).

While the user speaks over Echo, its voice is lowered to a fifth, never cut; the answer stays on screen and a finished one can be read aloud again (`replay`).

**Stopping is explicit and visible.** **Stop reply** sits beside the talk button whenever Echo is answering, waiting or speaking, in the floating Echo too; it cancels the reply and what waits. **End conversation** ends and forgets the conversation (`end`, or `POST /api/voice/conversation/end` from a window not attached). A failed reply says why and offers **Try again**. Every completed reply joins the history, played to the end or not; a stopped one does not (ADR 0043).

**Chat replies outlive their request.** `POST /api/chat` starts the reply on its own thread (`imprint/chat_replies.py`) and only watches it. Leaving the page, opening another chat or a dropped connection stops the watching; the reply records itself in the session as before. `GET /api/chat/sessions/{sid}` and the session list report `replying`; `GET /api/chat/sessions/{sid}/reply` replays the reply's events from the first, then the rest, so a chat opened again shows it whole. `POST /api/chat/sessions/{sid}/stop` is the Stop button, the only cancel; a second question in the same chat gets `409` until the reply ends. Replies are written one at a time, since leaving a chat mid-reply makes a second one easy to start and the local model is not safe to run from two threads: a question in another chat waits, showing "Waiting for the reply in another chat to finish…". The chat list marks a chat still answering.

## Rejected alternatives

- **Keep Echo connected while hidden.** It would keep a reply going while folded, but leaves a live microphone without a visible surface, which ADR 0135 rejected, and Echo talking from a hidden window. It also does not help a window that is destroyed (Close, the Floating Echo switch, a reload) or a dropped connection, which are the same loss.
- **Ask again from the window when it comes back.** The client could keep the question and resend it. A local answer can take minutes of research on a CPU (ADR 0101, 0127), so asking again repeats all of it, answers twice what was half answered, and is impossible once the page that held the question is gone.
- **Pause Echo's voice while the user speaks, instead of lowering it.** Capture and playback share one `AudioContext` in `VoiceClient`, so suspending it stops capture as well. Stopping and rescheduling buffers avoids that, but with speakers the pause removes the echo that opened the gate, the gate closes, playback resumes and the echo opens it again: a stutter the energy gate (ADR 0043) cannot tell from speech. Lowering keeps the audio continuous, and the whole answer stays readable and can be played again.
- **Interrupt only while Echo speaks, queue only while it thinks.** An accidental sound during playback would still discard the rest of a written answer, which is the loss reported.
- **A trained voice detector on every barge-in.** Silero is already loaded for "Hey Echo" (ADR 0137), but each utterance is transcribed by the speech worker anyway, and an empty or filler transcription identifies noise without a second model or another process.
- **Store voice turns in the chat store, like Chat.** Re-attachment would then survive an API restart. Voice has kept nothing on disk since ADR 0043 ("No raw audio, transcript or native prompt log is persisted by live sessions"), and the wake listener's explanation promises the same; changing that is the owner's privacy decision, not a side effect of this one. The in-memory conversation covers every case reported.

## Consequences

- Switching modes, speaking, typing, hiding, folding, floating, closing or reloading never loses a reply; the user sees it finish, or finished, in whichever window is open.
- The voice protocol grows: client controls `replay`, `end`, `cancel` with an `id`, and `overlap` on a spoken `turn`; server events `queued`, `start`, `ignored`, `stopped`, and `ready` with `resumed`, `busy`, `last_id` and `turns`. `played` is accepted and ignored. A dashboard page from before this change cancels on speech as before until it is reloaded.
- A reply that finishes with no window spends no time on speech, so it finishes sooner; read aloud later, it costs one more pass of text to speech.
- A spoken "stop" is heard only once the sentence being spoken is ready, because the worker takes one request at a time; with the small LFM model it waits for the whole reply. **Stop reply** is immediate.
- The history now includes replies the user may not have heard to the end; their text was on screen. A stopped reply is still left out.
- The API holds a detached conversation's text in memory for up to 30 minutes. The speech worker is released as soon as nothing needs it, as before.
- Chat replies in different chats now take turns; before, two tabs could run the local model from two threads at once. A remote provider waits too, which costs time but nothing else. Voice's chat route still runs beside Chat, as before.
- An API restart still loses a reply in progress, voice or Chat; Chat keeps what was stored before it.

## Invalidation conditions

- If people find that Echo keeps talking when they meant to interrupt it, in a usability check or support reports, then speech during playback should pause or end the spoken answer (the text stays) instead of lowering it.
- If acoustic tests in real rooms show the echo filter ignoring real questions, or Echo answering its own voice, then the 80 % run threshold changes or a trained detector with echo reference (AEC) replaces it.
- If the patched speech worker serves concurrent requests safely, then a question heard during a reply is transcribed at once and a spoken "stop" stops it mid-sentence.
- If the owner decides voice conversations may be stored, then the conversation moves to the chat store, re-attachment reads it from there and the 30-minute memory limit goes.
- If replies must survive an API restart, then their progress has to be stored as it is made, for voice and Chat alike.

## Validation

Executed on macOS (Apple M3) in the worktree for this change:

- `tests/test_voice.py` (`VoiceRunsToCompletionTests`): a reply goes on after its window closes, is kept with `away` and joins the history the next window's question sees; a window that comes back mid-reply gets the progress in `ready` and the rest of the events, with speech again; a question during a reply is `queued`, then `start`ed after `done`, with nothing cancelled; `[cough]`, "Um..." and silence are `ignored`; a spoken "Stop, please." cancels the reply and the waiting question; an `overlap` utterance that repeats the answer is `ignored` as echo and a real question is answered; `cancel` with an id withdraws one waiting question; `end` forgets the conversation; another model takes over after the reply; `replay` reads an answer again without touching history; a failed reply says why and the next question runs; a detached conversation is forgotten after the keep time; `POST /api/voice/conversation/end` refuses while another window holds it; noise, echo and stop phrases are classified as described. Existing voice tests pass, with the history test changed to keep a completed unplayed reply.
- `tests/test_chat_replies.py`: a chat reply goes on after its request closes and is stored; `/reply` replays from the start then the rest; `/stop` cancels and keeps what was written; a second question gets `409`; a reply in another chat waits, says so, and runs after the first.
- Full suite: `PYTHONPATH=.:tests python -m unittest discover -s tests`.
- Playwright (`imprint/ui/tests/voice.spec.ts`, `chat.spec.ts`, Chromium with a synthetic microphone): switching modes mid-reply sends no cancel; questions asked during a reply wait and can be removed; push-to-talk over a speaking reply sends the next question with `overlap` and cancels nothing; a failed answer offers Try again; a window that comes back picks up a running reply without the microphone, and shows a finished one with Play answer without connecting; hiding Echo closes the socket without `end` or `cancel` and showing it reconnects; the floating Echo keeps the newest answer and the waiting question on screen with Stop reply; Stop reply and End conversation; Chat keeps a reply going when another chat is opened and replays it whole, and Stop calls the API.
- `node --test scripts/voice-audio.test.mjs`: per-turn playback credit without `played`, Stop reply silencing late callbacks, and the voiced-frame count of an utterance.
- Real Electron 45 with the dashboard bundle: `ui-electron/smoke-orb.mjs` (Echo docked, folded and shown again) and `ui-electron/smoke-wake.mjs` (a wake turn, then a spoken turn, ended when asked) passed.
- Not executed: physical microphones and speakers on macOS, Windows and Linux; whether ducking keeps the energy gate from re-triggering on real speakers; what the LFM worker transcribes for real coughs and background noise; the echo filter in a real room; the time a spoken "stop" takes during a long sentence with the real worker; folding and showing Echo in the packaged app with the real API.
