# contabo_storage_manager — Agent Guide

## What this is

FastAPI backend (`packages/python-bridge/`) + nginx config that runs at **storage.noahcohn.com**.
It serves static files from `/data/files/` and exposes `/api/*` endpoints used by **flac_player** and other clients.

## Audio / flac_player integration

### How songs are stored

- Metadata lives in `/data/files/songs.json` (array of song objects)
- Audio files live in `/data/files/audio/music/{filename}` (usually `{id}_{title}.flac`)
- The `filename` field in each song entry is the **authoritative source** for constructing the public URL

### URL resolution rule (api.py `/api/songs`)

```
if song has filename  →  https://storage.noahcohn.com/files/audio/music/{filename}
elif url starts "/"   →  prepend https://storage.noahcohn.com
elif no url at all    →  https://storage.noahcohn.com/api/music/{id}   (proxy fallback)
```

**Do not add extra conditions here.** The `filename` path must always win so that songs
migrated from old backends (HuggingFace, etc.) get correct URLs automatically.

### Nginx /files/ block

`config/storage.noahcohn.com.conf` serves `/files/` directly from `/data/files/`.
It must include `Accept-Ranges bytes` so browsers can do range requests (audio seeking).
Do not remove range-related headers from that block.

### Diagnosing 404s on song load

Hit `GET /api/songs/debug` — it shows each song's filename, resolved URL, and whether
the file actually exists in `/data/files/audio/music/`. Common causes:
- `file_exists: false` — audio file is missing from disk; re-upload or sync from GCS
- `filename: null` — song was added without a filename; use `PATCH /api/songs/{id}` to set it,
  or re-upload via `POST /api/songs/upload`

### Syncing music from GCS

`POST /api/admin/sync-music` triggers `scripts/sync_gcs_music.py` in the background.
Check server logs after triggering it.

## Key files

| File | Purpose |
|------|---------|
| `packages/python-bridge/app/api.py` | Song CRUD, URL resolution, `/api/music/{id}` stream |
| `packages/python-bridge/app/config.py` | `static_base_url`, `files_dir`, CORS origins |
| `packages/python-bridge/app/main.py` | FastAPI app, router registration order |
| `config/storage.noahcohn.com.conf` | Nginx — proxies `/api/` to :8000, serves `/files/` statically |

## Known issues / blockers

(from a doc-freshness pass, 2026-09-07 — this file was 129 days stale relative to the code)

- `pyproject.toml`'s `dependencies` list is missing most of what the app actually imports (paramiko, asyncssh, jinja2, google-cloud-storage, watchdog, pydub, aiocache, gunicorn). `pip install -e .` alone will not get you a working dev environment — you also need `pip install -r packages/python-bridge/requirements.txt`.
- There are three parallel API implementations in `packages/python-bridge/app/`: `api.py` (the one actually wired into `main.py`), `api_full.py` (a separate GCS-backed app, not registered in `main.py` but still getting fixes and has its own test file), and `api_simple.py`/`api_shim.py` (an abandoned third attempt, imported by nothing). If you're touching song/API logic, make sure you're editing `api.py` — it's easy to end up in one of the other two by mistake.
- `deploy_router.py` (`/api/deploy`) is live in `main.py` but not documented in AGENTS.md's endpoint list.
- `/webhook/flac` and `/webhook/sequencer` in `webhooks.py` have unfinished handling (no `save_playlist`/`save_metadata`/`save_project` JSON support — see `TODO`s at `webhooks.py:358` and `:398`), and two dead `api.py.backup*` files sit unused in `app/`. Full detail in AGENTS.md's "Known Issues / Blockers".

## Things that break the integration

- Changing `static_base_url` in `config.py` without updating nginx alias path
- Removing `Accept-Ranges` from nginx `/files/` block — audio seeking breaks
- Changing the URL resolution logic in `/api/songs` to not prefer `filename` — old-URL songs 404
- Changing `/data/files/audio/music/` path without updating `files_dir` in config
