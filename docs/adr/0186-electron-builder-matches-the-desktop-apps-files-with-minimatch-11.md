---
title: ADR 0186 — electron-builder matches the desktop app's files with minimatch 11
description: The desktop app's build overrides the minimatch copies inside the advisory's vulnerable range with minimatch 11, and only those, after builds and a replay of every packaging decision showed it packages the same files.
---

- Status: Accepted
- Date: 2026-11-19
- Amends: [ADR 0013](0013-remediate-security-scan-dependencies.md) — one glob-library override, scoped to the vulnerable versions and shown not to change which files are packaged, is allowed
- Related: [ADR 0011](0011-nightly-trivy-security-scanning.md), [ADR 0111](0111-intel-macs-are-not-supported.md) (arm64-only macOS app), [security scanning](../security-scanning.md)

## Context

The nightly Trivy scan reports GHSA-g3hv-75wg-m249 (medium) in `ui-electron/package-lock.json`. With `optimizationLevel` 2 or higher, minimatch's first preprocessing phase lets a crafted pattern multiply its parts, followed by quadratic work. The advisory covers `>=7.0.0 <11.0.1` and names 11.0.1 as the only fix. minimatch publishes no fixed 9.x or 10.x: its `legacy-v9` tag is 9.0.9 and the 10 line ends at 10.2.6.

Two copies in the desktop build are affected, both development dependencies of electron-builder 26.15.3:

- minimatch 10.2.6, from `app-builder-lib` (`^10.2.5`). The newest releases, 26.18.1 and 27.0.0-alpha.14, still require `^10.2.5`, so there is no upstream release to move to.
- minimatch 9.0.9, from `@electron/universal` 2.0.3 (`^9.0.3`), which `app-builder-lib` 26 pins exactly. `@electron/universal` 3.0.4 and later drop minimatch.

Neither caller reaches the vulnerable path. `app-builder-lib` builds `new Minimatch(pattern, { dot: true })` in `out/fileMatcher.js` from the `files` patterns in `ui-electron/package.json` and its own defaults, and never sets `optimizationLevel`. `@electron/universal` calls `minimatch(file, pattern, { matchBase: true })` only when it merges x64 and arm64 apps into a universal macOS app, which Imprint does not build (ADR 0111). The finding stays in the report all the same.

[ADR 0013](0013-remediate-security-scan-dependencies.md) rejected blanket overrides of glob libraries because they "can silently change matching behavior". minimatch 11 does change matching: its changelog replaces `RegExp` matching with a non-recursive matcher, removes `makeRe()`, `regexp` and the `AST` class, and changes partial matching of `**` and how an empty path portion matches. electron-builder decides with these matches which files go into the installer.

## Decision

`ui-electron/package.json` overrides minimatch with a key limited to the advisory's range:

```json
"overrides": {
  "minimatch@>=7.0.0 <11.0.1": "^11.0.9"
}
```

npm applies a ranged override key only to dependency edges whose range intersects it. `app-builder-lib` and `@electron/universal` therefore get minimatch 11.0.9. The minimatch 3.1.5 and 5.1.9 copies of `@electron/asar`, `glob` 7, `dir-compare` and `filelist` lie outside the advisory and stay as they are. The override covers only the desktop app's build tooling. The packaged app has no `node_modules`, and the only trace of the override inside it is the `overrides` field of the `package.json` that electron-builder copies into it.

## Rejected alternatives

- **Wait for electron-builder.** No released or prerelease `app-builder-lib` requires minimatch 11: 26.18.1 and 27.0.0-alpha.14 both require `^10.2.5`. typescript-eslint did move, and the dashboard takes it as an ordinary upgrade (8.73.0, `^11.0.1`).
- **A blanket `"minimatch": "^11.0.9"` override.** This is the option ADR 0013 rejected, and it breaks callers written for minimatch 3. `@electron/asar` 3.4.1 calls `minimatch_1.default(...)` (`lib/asar.js` lines 54 and 147, for unpacked directories and files). minimatch 11's CommonJS build sets `__esModule` and exports no `default`, so those calls would throw.
- **Override `@electron/universal` to 3.x.** This removes the 9.0.9 copy but not `app-builder-lib`'s own 10.2.6, so the finding stays. It also replaces a package `app-builder-lib` 26 pins exactly.
- **Suppress the finding in `.trivyignore.yaml`.** The vulnerable path is unreachable, but [security scanning](../security-scanning.md) says not to suppress findings just to pass. ADR 0013 kept the unfixable diskcache advisory visible for the same reason. Here a fixed release exists and can be taken.

## Consequences

- The lock moves the hoisted minimatch from 10.2.6 to 11.0.9 and adds its dependency `@isaacs/nonrecursive-glob-match` 1.1.0. The 9.0.9 copy under `@electron/universal`, with its `brace-expansion` and `balanced-match`, deduplicates to 11.0.9. `npm ls minimatch` marks the copy as overridden and reports nothing invalid.
- minimatch 11.0.4 and later require Node 20 or 22 and newer. The release and CI workflows build the desktop app on Node 22.
- Each electron-builder update has to keep the override valid. It reaches only the edges it was written for while their ranges intersect `>=7.0.0 <11.0.1`.
- Nothing changes in the dashboard, the website or the Python runtime.

## Invalidation conditions

- If `app-builder-lib`, and the `@electron/universal` it installs, require minimatch 11.0.1 or later themselves, remove the override.
- If minimatch publishes a 9.x or 10.x release that fixes GHSA-g3hv-75wg-m249, remove the override and take that release.
- If electron-builder starts to use an interface minimatch 11 removed (`makeRe`, `regexp`, `AST`, `matchOne`, `debug`), or a build packages different files with the override than without it, remove the override and leave the finding visible with its reasoning.
- If Imprint starts building a universal macOS app, check `@electron/universal`'s `x64ArchFiles` and `infoPlistsToIgnore` matching under minimatch 11 before release.

## Validation

All of this ran on Linux x64 with Node 22.23.5 and electron-builder 26.15.3, on clean exports of the repository, before the change was committed.

- **Builds compared.** `electron-builder --linux --dir` built the app with and without the override. A preload hook on `app-builder-lib`'s `createFilter` logged every packaging decision. Both builds made the same 33 decisions (18 included), in a different order because the directory walk is asynchronous. The `app.asar` file lists are identical (17 entries), and so are their contents, except for the `overrides` field in `package.json`. The unpacked trees (70 other files) are identical.
- **Decisions replayed.** The two compiled pattern sets were replayed through `app-builder-lib`'s own `filter.js` over all 8,780 paths under `ui-electron`, `node_modules` included, with minimatch 10.2.6 and 11.0.9, under the `linux`, `win32` and `darwin` platform rules. That made 52,680 decisions with no difference. A default-style filter (`**/*` with electron-builder's exclusions and no `node_modules` exclusion) included 6,850 of the 8,780 paths. It made 26,340 decisions with no difference.
- **matchBase cases.** Seven `matchBase` cases in `@electron/universal`'s call form matched the same under both versions.
- **Not run before commit.** Native Windows (NSIS and portable) and macOS (dmg) packaging had not run before this record was written. The Release workflow builds them on the push of this change.
