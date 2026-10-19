# Documents (Steps 8 and 9): how to try them

Rules: decision D-010 in `DECISION_LOG.md`. Design: `docs/ARCHITECTURE.md` → "The life of one
uploaded document". In the UI, use the Documents panel on the right; the commands below do the same
through the API.

## Settings (`.env`, all optional)

| Setting | Default | Meaning |
|---------|---------|---------|
| `DOCUMENTS_ENABLED` | `true` | Adds the `/documents` routes (AUTH_MODE=jwt only) |
| `DOCUMENT_STORAGE_DIR` | `data/documents` | Files and the local catalog/index. Ignored by Git; back it up like a database |
| `DOCUMENT_MAX_FILE_MB` | `25` | Largest upload (D-010) |
| `DOCUMENT_PROCESSING_TIMEOUT_SECONDS` | `120` | A file that takes longer is refused as too complex |

## Try it (PowerShell, API on port 8001)

```powershell
.\venv\Scripts\Activate.ps1
$token = python -m tools.issue_dev_token --subject alice --tenant tenant-acme
$auth  = "Authorization: Bearer $token"

# Upload (answers 202: queued)
curl.exe -s -H $auth -F "file=@C:\path\to\terms.pdf" http://localhost:8001/documents

# Status of every document in the workspace (queued, processing, ready, failed)
curl.exe -s -H $auth http://localhost:8001/documents

# One document with its versions; retry after a technical failure; delete
curl.exe -s -H $auth http://localhost:8001/documents/<document_id>
curl.exe -s -X POST -H $auth http://localhost:8001/documents/<document_id>/retry
curl.exe -s -X DELETE -H $auth http://localhost:8001/documents/<document_id>
```

The token stays in the `$token` variable; do not paste it into chats or tickets.

## What you will see

| You upload | Result |
|------------|--------|
| A normal .docx / .pdf / .xlsx | `ready`, with the number of passages |
| A file with hidden sheets, rows, columns or hidden Word text | `ready`, and `notes` says what was skipped |
| A PDF where some pages are scans | `ready`, and `notes` counts the skipped pages |
| A fully scanned PDF | `failed`: "This PDF has no readable text…" (OCR is not supported yet) |
| The same file again | `duplicate: true`, nothing new stored |
| A file with the same name but new content | version 2; it replaces version 1 when ready |
| A renamed, macro, password-protected, .doc/.xls or over-25 MB file | refused at once with the reason |
| Another workspace's document ID | 404 "Document not found." |

## Search (Step 9)

In the UI: Documents panel → "Search documents". Or:

```powershell
curl.exe -s -H $auth -H "Content-Type: application/json" -d '{\"query\": \"late payment fee\"}' http://localhost:8001/documents/search
```

Each result is a passage quoted exactly as stored, with its citation (`terms.pdf · page 3`). Any of
the words may match ("payment" also finds "payments"); best matches come first. Nothing is
summarised or reworded. A search with no match says so; a search made only of filler words
("what is the") asks for better words.
