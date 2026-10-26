---
title: ADR 0075 — Microphone purpose in the macOS installer wrapper
description: Declare microphone usage in the enclosing installer app as well as the nested Electron app.
---

- Status: Accepted
- Date: 2026-10-26
- Amends: [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) — the enclosing installer app also requires microphone usage text

## Context

The local macOS 26.6.2 Apple Silicon crash report from 2026-10-26 at 10:28 identifies the nested Electron executable inside `/Applications/Imprint.app` and termination namespace `TCC`: the app attempted to access privacy-sensitive data without `NSMicrophoneUsageDescription`. The installed wrapper is version 0.25.0. Its nested Electron app already contains the usage string, carries an ad-hoc signature with identifier `tech.brightspaces.imprint`, and passes `codesign --verify --deep --strict`. The enclosing installer wrapper, identifier `com.alexandruleca.imprint`, contains no microphone usage string.

This contradicts ADR 0055's assumption that the enclosing app cannot matter because the shell starts through LaunchServices. Apple's [device-management policy schema](https://github.com/apple/device-management/blob/release/mdm/profiles/com.apple.TCC.configuration-profile-policy.yaml) documents that embedded helper tools inherit enclosing app permissions. Enclosing-app attribution is the leading explanation of this crash; an interactive corrected-install test is still required to establish that the change resolves it.

## Decision

Declare `NSMicrophoneUsageDescription` in the macOS installer's outer `Imprint.app` template as well as the Electron shell. Keep the shell's existing bundle signing. Test both distribution entry points for a nonempty usage string; standalone Electron packaging validation alone cannot cover the `.pkg` wrapper.

## Rejected alternatives

- Re-sign the nested Electron app again: the installed shell already passes strict signature verification and contains the required string.
- Reset microphone consent or reinstall the same package: neither supplies the missing declaration in the enclosing bundle.

## Consequences

New `.pkg` builds carry microphone purpose text in both apps. Existing installations need a corrected package; changing browser profiles or reinstalling an unchanged package does not change its metadata. This adds no automatic microphone access and does not bypass consent.

## Invalidation conditions

If a corrected installed package still terminates with the same TCC error, inspect the responsible application and its signature rather than treating the presence of both keys as proof of working consent. If the wrapper is removed from the distribution, remove its declaration check with that packaging change.

## Validation

On Apple Silicon macOS 26.6.2, inspected the installed crash report, both plists and the nested shell's signature; strict deep signature verification passed. `plutil -lint` validates the corrected template. The regression test checks both plists/configurations, and ADR structure tests validate the record links.

Interactive consent and physical microphone capture with a corrected installed `.pkg` remain untested, as does Intel macOS. No real client profile, microphone permission or installed application was changed during these checks.
