# FlowOne chat UI (Phase 1)

A React + TypeScript web page for asking FlowOne questions. Phase 1 answers from structured
data only; document upload and document evidence arrive in Phase 2 (after roadmap Steps 8–9).

The UI talks **only** to the public HTTP API (`/me`, `/ask`, `/clarify`). It never imports
backend code and never decides what a user may see: the server does that.

## Run it (PowerShell)

1. Start the API in per-user sign-in mode (see `docs/project/IDENTITY.md`): `AUTH_MODE=jwt`.
2. In another terminal:

```powershell
cd ui
npm install          # first time only
npm run dev          # http://localhost:5173
```

3. Create a development token and paste it on the sign-in screen:

```powershell
python -m tools.issue_dev_token --subject alice --tenant tenant-acme --role credit_manager
```

The development server forwards `/api/*` to the FlowOne API, so the browser never needs
cross-origin access. The API address defaults to `http://localhost:8000`; if FlowOne runs
elsewhere, create `ui/.env.local` (git-ignored) with one line, then restart `npm run dev`:

```
FLOWONE_API_URL=http://localhost:8001
```

If port 5173 is busy, Vite picks the next free port (5174, 5175, ...); use the address it prints.

Troubleshooting: "The UI reached a server without FlowOne sign-in" means the address above points
at a different program, or the API is not running with `AUTH_MODE=jwt`.

## Test and build

```powershell
npm test         # unit tests with a fake API (no backend needed)
npm run build    # type-check + production build into dist/
```

## Where things are

| Folder | Job |
|--------|-----|
| `src/api/` | The only code that talks HTTP: request types, error kinds, client |
| `src/state/` | What the chat remembers and its safety rules (one request at a time, late answers dropped, choices used once, conversations kept per company) |
| `src/components/` | What you see: one file per piece of the screen |
| `src/format/` | Display helpers: amounts with currency codes; never calculates |
| `src/test/` | Tests, including the negative scenarios in `docs/project/ROADMAP.md` |

## Rules

1. Show what the server returned; never recalculate numbers or add amounts across currencies.
2. Show all server notices (date periods, missing values, groups not listed).
3. Text from data is plain text; never render it as HTML (`dangerouslySetInnerHTML` is not used).
4. The token stays in memory only (closing or reloading the tab signs you out in Phase 1).
5. Never show placeholder content: unavailable features say so.
