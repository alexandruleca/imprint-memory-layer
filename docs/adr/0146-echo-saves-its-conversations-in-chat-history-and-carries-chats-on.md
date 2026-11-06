---
title: ADR 0146 — Echo saves its conversations in Chat history and carries chats on
description: Echo's conversations are saved as text in Chat history on this device unless the user turns that off, a look or model change keeps the conversation, and Echo can carry on any chat from Chat history.
---

- Status: Accepted
- Date: 2026-11-06
- Scope: Echo (Voice page and floating companion) and the dashboard Chat page; real microphones and rooms are unvalidated (see Validation)
- Amends:
  - [ADR 0043](0043-live-voice-and-echo-companion.md) — a live session's transcript is saved in Chat history unless the user turns saving off; audio and native prompt logs still are not
  - [ADR 0071](0071-each-look-speaks-in-its-own-voice.md) — a new look or reasoning model keeps the conversation's history instead of clearing it
  - [ADR 0141](0141-chat-summarizes-what-no-longer-fits-and-can-look-back.md) — a voice conversation's history and running summary are saved with its chat, not kept for the session only
  - [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md) — the conversation is still held in memory by the API, and its exchanges are also written to Chat history; a reply stopped partway joins the history as far as it got
- Related: [ADR 0137](0137-echo-wakes-on-hey-echo-from-a-hidden-desktop-listener.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md), [live voice](../live-voice.md), [dashboard](../dashboard.md), [API](../api.md)

## Context

Since ADR 0043 Echo has kept nothing on disk. ADR 0145 kept the conversation in the API's memory so it outlives its window, and left saving it to the user's choice: "changing that is the owner's privacy decision". The cost of memory-only voice:

- **Answers are lost.** An answer read 31 minutes after its window closed, or after the API restarted, is gone, and a spoken answer is harder to reread than a typed one.
- **No way back.** A voice conversation cannot be continued later, by voice or by typing.
- **Two histories.** A question asked in Chat cannot be followed up by voice with what was said there.

On 2026-11-06 the user decided that voice conversations are saved in Chat history like regular chats, and asked that Echo can carry on an older chat as well.

## Decision

**Echo saves its conversations in Chat history.**

- **What is saved.** Each answered exchange is saved as text: the question as Echo heard it, the tool results of the reply and the answer. The conversation becomes one chat of kind `voice`, titled after its first question; a carried-on chat gets the new exchanges.
- **What is not.** Audio, wake probes ("Hey Echo"), noise, Echo hearing itself, a spoken "stop" and replays. A reply stopped or failed partway is saved as far as it got, the way Chat keeps one, and the next question sees it.
- **Same numbering.** The conversation's history and its running summary (ADR 0141) are written with the numbers Chat uses, so the summary still points at the right messages when the chat is opened in Chat or carried on again.
- **Where.** Chat history is the per-workspace SQLite database on this device. It is not synced, and the session learner and Organize do not read it.

**The user decides.** The setting `voice.save_conversations` (default on) is a switch in Companion settings and in Settings. The conversation reads it when it starts:

- turning it off stops saving the conversation in progress from its next answer;
- turning it on saves the next conversation, not the rest of the current one, which would leave a chat with gaps.

Echo's status line says "saved in Chat" or "not saved". The "Hey Echo" explanation says that a woken question is saved like any other. A chat deleted in Chat while Echo talks is not recreated: the rest of that conversation is not saved.

**Echo carries chats on.** Any chat, typed or spoken, can be continued by voice:

- **Earlier chats** in Echo lists the eight most recent chats.
- **Continue with Echo** on the Chat page opens the Voice page ready to continue that chat. The conversation starts on the user's click, as every conversation does (ADR 0069).

The chat's messages and summary become the conversation's, its last eight exchanges show as turns ("Continuing “title”"), and new exchanges are added to it. `POST /api/voice/sessions` takes `chat`; an unknown chat is refused with 404. A request without `chat` carries on the open conversation as before. A request for another chat takes over an idle conversation at once, and a busy one once its reply is answered (ADR 0145).

**A look or model change keeps the conversation.** ADR 0071 cleared the history on such a change because "the history is transient anyway". Now that it is saved, clearing would split one conversation into two chats. Another look or model keeps the history and the chat. Another workspace still starts afresh.

## Rejected alternatives

- **Memory only (ADR 0043, 0145).** The user decided against it. It keeps the losses listed under Context.
- **Saving audio.** It is not needed to read or continue a conversation. It would be about 2.9 MB per minute of 24 kHz PCM16, and it is more sensitive than text.
- **A separate store or page for voice.** Chat history already lists, renames and deletes chats, summarizes long ones and reads them back with `conversation_history`. A separate store would duplicate all of that, and a conversation could not move between typing and speaking.
- **Reading the setting on every answer and resuming when it is turned back on.** The chat would have gaps, and the running summary's numbering would no longer match the saved messages.
- **Off by default.** The user asked for voice to be saved like chats. The switch and the status line make the choice visible.
- **Starting the conversation as soon as Chat sends a chat to Echo.** Starting audio needs a click in the browser, and the microphone opens only on the user's intent (ADR 0069). The Voice page waits with **Continue “title”** instead.

## Consequences

- **Voice is on disk.** What Echo hears and answers stays in Chat history until the user deletes the chat or turns saving off. A misheard question is saved as Echo heard it.
- **History grows.** Each conversation adds a chat, a marker distinguishes voice chats in the list, and short ones accumulate.
- **Conversations continue.** A spoken conversation can be continued by typing, and a typed one by voice. Echo restarted, or 30 minutes away, no longer loses the answers.
- **A stopped reply now joins the history** as far as it got. It used to be dropped from memory.
- **Clients must keep numbering.** A look change mid-conversation now resumes it, so a client numbers its next question after the `last_id` that `ready` reports, as the dashboard does.

## Invalidation conditions

- If voice conversations should follow the user to another device, then Chat history must sync. Today it does not.
- If voice chats crowd out typed ones in the list, then group them, or save only conversations with more than one exchange.
- If misheard questions saved as heard mislead later answers, then save the recognition only after the user sees it, or mark spoken questions.

## Validation

- **`tests/test_voice.py`:**
  - answered exchanges, with tool results and the running summary, are saved with Chat's numbering as one `voice` chat;
  - noise and replays are not saved; a stopped reply is saved as far as it got, and the next question sees it;
  - turning saving off stops it at once, and turning it on saves the next conversation; with saving off nothing is written;
  - a chat from Chat history is carried on with its messages, summary and last exchanges, and new exchanges are added to it;
  - an unknown chat is refused;
  - another chat takes over an idle conversation;
  - deleting the chat stops saving;
  - a new look or model keeps the conversation and its numbering.
- **`tests/test_chat_replies.py`:** an older chat database lists its chats as `chat`, and the API reports each chat's kind.
- **`tests/voice.spec.ts` and `tests/chat.spec.ts`:**
  - Earlier chats carries a chat on and numbers new questions after it;
  - Continue with Echo waits for a click and can start a new conversation instead;
  - the saving switch writes the setting and the status line follows it;
  - the Chat list marks a conversation with Echo.
- **Not verified:** a saved conversation spoken through a real microphone, and carrying a long chat on with the local model on hardware.
