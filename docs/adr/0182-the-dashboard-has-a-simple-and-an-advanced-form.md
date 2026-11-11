---
title: ADR 0182 — The dashboard has a simple and an advanced form
description: One dashboard is shown two ways, chosen per device by the ui.mode setting; the simple form lists eleven pages under plain names, trims the shared pages and offers settings as plain choices over the same keys, and nothing is disabled in either.
---

- Status: Accepted
- Date: 2026-11-11
- Related: [ADR 0183](0183-each-page-has-a-short-tutorial-that-can-be-skipped.md) (tutorials), [ADR 0154](0154-the-dashboard-and-desktop-app-follow-a-light-dark-or-system-theme.md) (the theme, a per-browser choice), [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md) and [ADR 0143](0143-agents-receive-generalised-guidelines-core-memories-are-kept-as-records.md) (the decisions Home's inbox makes), [ADR 0089](0089-one-progress-record-for-the-running-job.md) (the running job on every page), [dashboard](../dashboard.md#simple-and-advanced), [configuration](../configuration.md)

## Context

The dashboard had 18 pages in the sidebar and 93 settings in 19 sections. Its words are those of the implementation: sources, chunks, ingest, embedding, tagging, the queue. The owner asked for a switch to a simplified dashboard for people who are not technical, simpler in use and in settings, with the current one kept as the advanced mode for setting Imprint up in detail and looking into problems.

Two constraints came with it. The advanced dashboard is what the documentation, the support answers and the 287 dashboard tests describe, so it must not change. And the person who needs the advanced view of a problem is often looking at the simple user's installation, so nothing may exist in one mode only.

## Decision

**The dashboard is shown two ways over the same pages, data and settings.** Advanced is the dashboard as it was. Simple hides and rewords; it disables nothing. Every address works in both.

**The mode is the `ui.mode` setting** (`simple` or `advanced`, env `IMPRINT_UI_MODE`), kept per device in `config.json`. The dashboard reads it with `ui.tutorials` from `GET /api/ui` and keeps the last answer in the browser (`imprint.ui.mode`), so the navigation is drawn in the right mode before the API has answered; nothing is drawn until one of the two is known. An answer without a mode, or none, leaves the remembered mode, else Advanced. The switch is a top-bar menu, the welcome ([ADR 0183](0183-each-page-has-a-short-tutorial-that-can-be-skipped.md)) and Settings; a switch that cannot be saved is put back, and other open windows follow it.

**The first mode is chosen once per installation,** by the dashboard API at its first start with this setting (`imprint/ui_mode.py`): `advanced` when `data/queue.sqlite3` holds a job or `data/agent_activity.sqlite3` a session, `simple` otherwise. It is written to `config.json` either way, so a new installation is not taken for a used one once it has history. A value already there is kept.

**Simple lists eleven pages** (`imprint/ui/src/lib/pages.ts`): Home (Dashboard), Chat, Search, Echo (Voice), Recordings, Reminders; Library (Projects), About you (Core memories), Skills; Devices (Sync), Settings. Graph, Topics, Sources, Organize, Queue, Agents and Commands are not listed; opened by address they show a note with a switch to Advanced.

**What replaces a hidden page stays reachable from Simple:**

- Organize's review and Core memories' proposals and conflicts appear on Home as **Needs your review**, each with its two or three decisions and an undo. They are the same writes (`/api/organize/decide`, `/api/core/{id}`, `/api/core/{id}/resolve`), so the review pages show them decided.
- The Queue appears as the top-bar pill, which names the running job in plain words and opens its progress and what waits, and as **Try again** on a job that did not finish.
- The workspace switcher is left out while there is one workspace.

**The shared pages leave out their technical parts** (the list is in [dashboard](../dashboard.md#simple-and-advanced)): filters, ranking details, chunks, the session budget, relays, snapshots, scheduled jobs, the command behind an action. A tab or section left out still opens by its address.

**Settings in Simple are choices over the same keys** (`imprint/config_simple.py`, `GET /api/settings/simple`, `PUT /api/settings/simple/{item}`): 24 items in seven groups, each a switch on one boolean setting, a choice among a setting's values under plainer names, or a preset that writes several settings. The catalog stores nothing. Stored values that match no option show as Custom and are left alone until an option is picked. An option that needs a provider's API key is unavailable while the key's environment variable is unset; an item whose setting an environment variable holds is read-only. Each group links to its advanced sections, which open on the same page.

## Rejected alternatives

- **Keeping the mode per browser, like the theme (ADR 0154).** The desktop app, a browser tab and Echo's window would disagree, and nothing could tell a new installation from a used one: a browser profile holds no evidence of earlier use, while the data directory does.
- **A second set of pages for the simple dashboard.** Two implementations of Search, Projects or Core memories would drift, and every fix would be made twice. The pages read the mode and leave parts out; the largest change to a shared page (Projects) touches about 50 lines.
- **Turning features off in Simple.** The same installation is looked at in both modes: a page that did not work in Simple would break links from documentation, toasts and other pages, and would make "switch to Advanced" a precondition for support. Hidden pages therefore open with a note instead.
- **Choosing the first mode in the dashboard,** from the memory count or the browser's storage. A device whose owner first opens the dashboard after weeks of agent use would be offered the welcome as if new, and two windows opened together could choose differently. The API chooses once, before any window asks.
- **Leaving `ui.mode` unset for new installations** and treating unset as simple. The next start would find job history and settle on advanced. The choice is written either way.
- **A list of simple settings in the dashboard's source.** Nothing would check its keys and values against the schema. On the server, `tests/test_config_simple.py` checks that every key exists, every switch is a boolean, every option's values pass their setting's parser, and one option of every preset is the defaults.
- **Simple settings with values of their own** (a "quality" setting resolved at run time). The CLI, environment variables and the advanced page would then describe a different state than the simple page. A simple choice writes the real keys, and what does not match shows as Custom.
- **Folding Search into Chat** for a shorter sidebar. Asking a question and finding a passage are different tasks, and Search answers without a model. The owner kept both.
- **Leaving Skills out of Simple.** Proposed because skills concern AI clients; the owner asked to keep it, unchanged.

## Consequences

- Advanced is unchanged: the 287 dashboard tests that existed pass without edits, since a service that names no mode leaves Advanced.
- One more setting group (Dashboard) and three endpoints. `config.json` gains `ui.mode` on every installation at its next dashboard start, and `ui.tutorials: false` on those already in use.
- A page added later must be given a simple name or left advanced-only in `pages.ts`, and a setting worth offering to everyone an item in `config_simple.py`.
- Simple's names differ from the advanced ones (Library is Projects, About you is Core memories, Devices is Sync, Echo is Voice, Home is Dashboard). Documentation and messages that name a page use the advanced name; the address is the same.
- Known gaps. Provider API keys are still environment variables, so Simple can say a key is missing but cannot take one. Dialogs shared with the advanced dashboard (moving a project, the workspace manager, the source reader) keep their wording, including "workspace" and "source". The desktop app's tray and floating button do not follow the mode. An installation used only through the CLI's own commands, without a job in the dashboard's queue or an agent session, counts as new.

## Invalidation conditions

- If support or bug reports show people stuck in Simple because a page or setting they needed was not listed and the note did not lead them to it, list that page or setting in Simple rather than adding a third mode.
- If provider API keys become stored settings, offer them in Simple's Assistant group and drop the unavailable state.
- If more than a few shared pages need different layouts, not just fewer parts, in Simple, reopen the rejected second set of pages for those pages only.
- If installations are found settled on the wrong mode at first start (a used one on simple, a new one on advanced), change the evidence `ui_mode.used_before` reads; the setting and the switch stay.

## Validation

- Python, Windows 11, the installation's Python 3.13 venv, from a native copy of the working tree: `tests/test_ui_mode.py` (the first mode for a new and a used installation, settled once, an existing choice and other settings kept, an unreadable store, `GET /api/ui`), `tests/test_config_simple.py` (the catalog against the schema, presets, Custom, conditions, a provider without its key, a setting an environment variable holds, the report, the two endpoints) and `tests/test_config_help.py`.
- Playwright, Chromium on Linux (WSL), against mocked API answers: `ui-mode.spec.ts` (both navigations, the switch and its failure, a service that names no mode, the note on an advanced page, a second window), `simple-settings.spec.ts`, `home-simple.spec.ts` (the inbox's decisions and undo, a refused move, a failed job, the plain activity pill and job toast) and `simple-pages.spec.ts` (Search, Reminders, Core memories, Sync and Projects in both modes), with the simple chat row in `chat.spec.ts`.
- The whole suites: 1,292 Python tests pass on Windows 11 (22 skipped), and 367 Playwright tests pass, the 287 that existed among them without edits.
- Not run: the dashboard against a real service, the first-start choice on a real upgraded installation, the desktop app on any platform, and macOS. No person who is not technical has used the simple dashboard yet; its page list and wording are the owner's and the implementer's judgment.
