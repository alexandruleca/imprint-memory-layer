# Security policy

## Disclaimer

This document is **informational only**. It is **not** legal advice and does **not** create a contract, warranty, or enforceable obligation of any kind. Maintainers may change, pause, or withdraw this policy at any time. **The software is provided on an “AS IS” basis**; there is **no** warranty that it is free of vulnerabilities or that reports will receive a response, assessment, or fix. See the project [LICENSE](LICENSE) for license terms and applicable disclaimers. Nothing here limits those disclaimers or any rights or remedies available under applicable law.

Thank you for helping improve Imprint’s security. Please read the rest of this page before discussing security-sensitive issues in public.

## Reporting a vulnerability

**Please do not open a public GitHub issue** for undisclosed security problems.

Use **[GitHub private vulnerability reporting](https://github.com/alexandruleca/imprint-memory-layer/security/advisories/new)** for this repository so maintainers can review details privately.

Include as much of the following as you can:

- A clear description of the issue and its impact (confidentiality, integrity, availability, local vs remote, privilege required).
- Affected component (Go CLI, Python MCP server, installer scripts, sync/relay, Qdrant integration, etc.) and version or commit if known.
- Steps to reproduce, or a proof-of-concept, **without** exploiting real users or systems.
- Whether you believe the issue is already exploitable in a default install, or only under specific configuration.

If GitHub private reporting is unavailable for any reason, open a **draft** security advisory or contact the repository maintainers through GitHub (e.g. [@alexandruleca](https://github.com/alexandruleca)) and ask for a secure channel — still avoid posting exploit details in public issues or discussions.

## What to expect

- Maintainers may, on a **best-effort** basis, acknowledge reports when practical. There is **no** guaranteed response time, triage outcome, severity rating, patch, release, or public advisory.
- Maintainers may ask follow-up questions or request a retest on a fix branch.
- Credit in release notes or advisories may be discussed when a fix ships; say how you wish to be named (or if you prefer to stay anonymous).

## Disclosure

Coordinated disclosure is **preferred when practical**: maintainers may work toward a fix and then publish a GitHub Security Advisory or release notes. Reporters are asked not to publish non-public exploit details until maintainers have had a reasonable chance to respond, **except** where immediate public warning is legally required or clearly necessary to reduce active harm. Even then, sharing the minimum necessary detail is appreciated. Specific timelines and disclosure decisions remain **at maintainers’ discretion** and depend on severity, complexity, and capacity.

## Scope (in brief)

**Generally in scope**

- Remote or local code execution, path traversal, or unsafe deserialization in Imprint’s own code (Go or Python), default or documented configurations.
- Issues in bundled or documented install/update paths that could lead to compromise of the user’s machine or data directory.
- Authentication, authorization, or transport flaws in the **sync / relay** surfaces that ship in this repo, when used as documented.

**Generally out of scope or lower priority**

- Findings that require the victim to run untrusted code or paste untrusted content into a shell **and** that are inherent to that action (e.g. “running `curl | bash` with a malicious URL”) without a defect in our scripts.
- Denial of service by exhausting local resources on the reporter’s own machine when no other user is affected.
- Vulnerabilities in **third-party** dependencies or tools (Qdrant, ONNX Runtime, host editors, MCP clients): report those to the upstream project; we still welcome a heads-up so we can bump versions or document mitigations.
- Misconfiguration (e.g. exposing Qdrant or relay ports to the public internet without firewalling) when defaults are local-only and documented.

If you are unsure whether something is in scope, report it privately anyway.

## Supported versions

Where maintainers choose to ship fixes, those changes **may** target the **current** stable line on `main` and, when applicable, the active development line on `dev`. Older tags **may not** receive backports. Upgrading to the latest release you trust remains your responsibility.

## Research and the law

Follow this policy **and** applicable laws and third-party terms (e.g. authorized testing only on systems and accounts you own or have **written** permission to test). Nothing here authorizes unauthorized access, exfiltration of others’ data, denial-of-service against shared infrastructure, or harassment. Maintainers may involve service providers or counsel when appropriate.

---

This policy may be updated at any time. The copy in the repository is a convenience; it is not offered as a stable legal instrument.
