# Web Sequencer Storage Migration

This document describes the migration from HuggingFace Spaces storage to the Contabo VPS storage for the web_sequencer music app.

## Overview

The web_sequencer now uses the Contabo VPS at `https://storage.noahcohn.com` instead of the HuggingFace Spaces backend at `https://ford442-storage-manager.hf.space`.

> **Do not use port 8000 from browsers.** The Python bridge listens on plain HTTP on :8000;
> TLS is terminated by nginx on 443 (`config/storage.noahcohn.com.conf`), which proxies
> `/api/` and `/webhook/` to the bridge. An `https://…:8000` URL fails in Chromium with
> `net::ERR_SSL_PROTOCOL_ERROR`. Always use `https://storage.noahcohn.com`.

## New API Endpoints

The following REST API endpoints have been added for music app storage:

### Songs
- `GET /api/songs` - List all songs with optional filtering
- `GET /api/songs/{id}` - Get specific song data
- `POST /api/songs` - Upload new song
- `DELETE /api/songs/{id}` - Delete song
- `PATCH /api/songs/{id}` - Update song (versioning)

### Patterns
- `GET /api/patterns` - List all patterns
- `POST /api/patterns` - Upload new pattern

### Banks
- `GET /api/banks` - List all banks

### Samples
- `GET /api/samples` - List all samples
- `POST /api/samples` - Upload audio sample (multipart/form-data)
- `GET /api/samples/{id}` - Get sample metadata

### Compatibility
- `GET /api/items` - Combined list endpoint (HuggingFace compatible)
- `GET /api/sequencer/health` - Health check

### TTS Models
- `GET /models/tts/list` - List TTS models
- `GET /models/tts/health` - Check TTS model availability
- `GET /models/tts/{filename}` - Serve TTS model files

## File Storage Structure

```
/data/files/
├── sequencer/
│   ├── songs/
│   │   ├── _songs.json          # Index file
│   │   ├── {uuid}.json          # Song data files
│   │   └── ...
│   ├── patterns/
│   │   ├── _patterns.json       # Index file
│   │   └── {uuid}.json
│   ├── banks/
│   │   ├── _banks.json          # Index file
│   │   └── {uuid}.json
│   ├── samples/
│   │   ├── _samples.json        # Index file
│   │   ├── {uuid}.wav
│   │   └── {uuid}.mp3
│   └── ai-generated/
│       ├── _ai-generated.json   # Index file
│       └── {uuid}.json
├── models/
│   └── tts/                     # TTS models (optional)
│       ├── duration_predictor.onnx
│       ├── text_encoder.onnx
│       ├── vector_estimator.onnx
│       ├── vocoder.onnx
│       ├── tts.json
│       ├── unicode_indexer.json
│       └── voice_styles/
│           ├── M1.json
│           ├── M2.json
│           ├── F1.json
│           └── F2.json
└── ...
```

## Configuration

### Environment Variables

The sequencer router uses the standard configuration from `config.py`:

```bash
# Storage directory (inside container)
FILES_DIR=/data/files

# Static file serving URL
STATIC_BASE_URL=http://localhost:8000/files
```

### CORS

CORS is configured in `main.py` to allow all origins:

```python
allow_origins=["*"]
allow_methods=["*"]
allow_headers=["*"]
```

## Web Sequencer Changes

### CloudStorage.ts

Updated the API base URL:

```typescript
// Old
export const API_BASE_URL = "https://ford442-storage-manager.hf.space";

// New
export const API_BASE_URL = "https://storage.noahcohn.com";
```

### TTS Model Loading (Optional)

To serve TTS models from the VPS instead of HuggingFace:

1. Place TTS model files in `/data/files/models/tts/` on the VPS
2. Update `Supertonic.ts` to load from VPS:

```typescript
// Instead of loading from HuggingFace:
// const baseUrl = 'https://huggingface.co/Supertone/supertonic/resolve/main/onnx';

// Load from VPS:
const baseUrl = 'https://storage.noahcohn.com/models/tts';
```

## Migration Checklist

- [x] Create `sequencer_router.py` with REST API endpoints
- [x] Register router in `main.py`
- [x] Update `CloudStorage.ts` with new base URL
- [x] Update `AGENTS.md` documentation
- [x] Add TTS model serving endpoints
- [ ] Deploy updated contabo_storage_manager to VPS
- [ ] Copy existing songs from HuggingFace to VPS (if needed)
- [ ] Test all endpoints
- [ ] Update DNS if needed

## Testing

Test the endpoints:

```bash
# Health check
curl https://storage.noahcohn.com/api/sequencer/health

# List songs
curl https://storage.noahcohn.com/api/songs

# Upload song
curl -X POST https://storage.noahcohn.com/api/songs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Song",
    "author": "Test User",
    "description": "A test song",
    "type": "song",
    "data": {"bpm": 120, "tracks": []},
    "tags": ["test"]
  }'

# Check TTS models
curl https://storage.noahcohn.com/models/tts/health
```

## Troubleshooting

### CORS Issues

If you see CORS errors in the browser:
1. Check that the CORS middleware is properly configured in `main.py`
2. Verify the `OPTIONS` handler is working: `curl -X OPTIONS https://storage.noahcohn.com/api/songs`

### File Not Found

If songs are not found after upload:
1. Check the `FILES_DIR` environment variable is set correctly
2. Verify the directory structure exists: `ls -la /data/files/sequencer/`
3. Check index files are being created: `cat /data/files/sequencer/songs/_songs.json`

### FTP Sync Issues

If files are not syncing to the external FTP:
1. Check FTP credentials in environment variables
2. Look at logs: `docker logs contabo-storage-python`
3. Verify FTP connection manually

## Rollback

To rollback to HuggingFace storage:

1. Change `API_BASE_URL` in `web_sequencer/src/services/CloudStorage.ts` back to `"https://ford442-storage-manager.hf.space"`
2. Rebuild and redeploy the web_sequencer
