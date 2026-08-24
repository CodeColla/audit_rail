# `webui/` conventions

Loaded automatically when working under `webui/`. Assumes you've already read the root
`CLAUDE.md`. Lighter than `api/CLAUDE.md` — issue #5's standards request was backend-flavoured
(try/except, logging, main-file shape), so this is mostly a map of what's already established,
not new policy.

## Layout

- `src/pages/` is grouped by feature (`documents`, `controls`, `registers`, `audits`,
  `people`, `admin`, `auth`) — not one flat folder.
- `src/components/` is shared UI used across more than one page.
- `src/lib/ui.tsx` is the shared design-system layer: `cn()` (tailwind-merge + clsx), `Card`,
  `Table`/`Td`, `Drawer`, `Modal`, `Pill`, `PageHead`, `Loading`, `Empty`, plus small shared
  constants (e.g. `SHELL_HEADER_H` / `STICKY_BELOW_HEADER`) — check here before adding a new
  one-off styled primitive.
- `src/lib/api.ts` is the single axios instance (`baseURL: "/api"`, bearer token from
  `localStorage`, 401 → redirect to `/login`). Never construct a second client.

## Data fetching

- `@tanstack/react-query` throughout. `useQuery({ queryKey: [...], queryFn: () => get(...) })`
  for reads, `useMutation` + `qc.invalidateQueries({ queryKey: [...] })` on success for
  writes. Query keys are arrays, and a partial key (e.g. `["grid"]`) intentionally invalidates
  every query whose key starts with it (`["grid", id]` included) — this is relied on, not
  accidental.
- A file/blob preview (evidence, a register attachment) uses `AttachmentLink`
  (`components/AttachmentLink.tsx`) — it fetches bytes and previews them. A link to a RECORD
  with its own page (a document, an incident, an asset) is a plain `<Link>` with an icon, not
  `AttachmentLink` — don't reach for the file-preview component just because both look like
  "a linked thing."

## A real gotcha worth knowing before you hit it again

`position: sticky` breaks silently — not visually, not via any error — if its nearest
ancestor with non-`visible` overflow is a box that never actually scrolls itself. The sticky
element's offset then tracks page scroll 1:1 forever, never clamping, which looks identical to
`position: static`. This bit the documents editor toolbar (P7-S4): an `overflow-hidden` used
purely to clip rounded corners was silently eating `position: sticky`. If a sticky element
"does nothing," check every ancestor's `overflow` before assuming it's a z-index or CSS
specificity problem.

## Verifying a UI change

Playwright is set up with a userspace Chromium (see `e2e.sh`'s own comments for the Node 22 +
library-path setup this machine needs) — a change isn't verified by `tsc` alone. Sign up a
fresh org in a real browser session and drive the actual feature before calling a UI change
done; several real bugs this project has hit were invisible to type-checking and only caught
by looking at the rendered page.
