This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

## Pages

| Route | Purpose |
|---|---|
| `/` | Dashboard (overview, facets, quick ingest) |
| `/graph` | Obsidian-style project/topic graph |
| `/search` | Semantic search over indexed memories |
| `/chat` | Chat against the memory layer |
| `/topics` | Topic list + per-topic detail |
| `/projects` | Project list + per-project detail |
| `/sources` | Indexed files / URLs |
| `/sync` | Peer sync (export/import/relay) |
| `/commands` | Run imprint CLI commands + view config |
| `/queue` | **Queue** — active + queued + history for ingest/refresh/retag jobs, with cancel |
| `/settings` | Settings (`imprint config`) |

The dashboard loads the bundled Inter variable font through `next/font/local` (ADR 0008).

## Components

Controls and overlays come from the shadcn components in `src/components/ui` (base-nova style on `@base-ui/react`, ADR 0039). They are generated source owned by this repository; the generator is not a dependency (ADR 0013). To add one, generate it in a throwaway project (`npx shadcn@4.22.1 add <name> --cwd <scratch copy>`), copy the new file into `src/components/ui` and import `cn` from `@/lib/utils`.

- Hover hints: `Hint` (`src/components/hint.tsx`) wraps a control in a `Tooltip`; icon-only controls still need an `aria-label`.
- Spinners beside text: `Spinner` from `src/components/loaders.tsx` is hidden from assistive technology, since the text already says what is loading.
- Feedback: `toast` from `sonner`; the layout mounts the `Toaster` once. `JobToasts` announces queued jobs that end.
- Ingest and refresh: `enqueueCommand` (`src/lib/api.ts`) asks to approve a root the server refused (409 `root_not_approved`) through the one `RootApprovalDialog` in the layout, then submits the job again.
- Desktop window: elements with `app-drag` move the frameless window. `globals.css` turns the handles off while a dialog, alert dialog, sheet, popover, menu or select is open, keyed on their `data-slot` names. A new overlay component must be added to that rule.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
