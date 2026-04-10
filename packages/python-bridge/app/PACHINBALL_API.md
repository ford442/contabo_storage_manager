# Pachinball Content API

Full CRUD API for managing Pachinball game content.

## Base URL
```
https://storage.noahcohn.com/api
```

## Maps

### List Maps
```http
GET /maps
```
Returns all map configurations.

### Get Single Map
```http
GET /maps/{map_id}
```

### Create Map
```http
POST /maps
Content-Type: application/json

{
  "id": "my-map",
  "name": "My Map",
  "baseColor": "#00d9ff",
  "accentColor": "#ffffff",
  "glowIntensity": 1.0,
  "backgroundPattern": "hex",
  "animationSpeed": 0.5,
  "musicTrackId": "my-map",
  "shaderUrl": "/pachinball/shaders/my-map.glsl",
  "mode": "fixed",
  "worldLength": 200
}
```

### Update Map
```http
PUT /maps/{map_id}
Content-Type: application/json

{ ... map config ... }
```

### Delete Map
```http
DELETE /maps/{map_id}
```

## Music

### List Tracks
```http
GET /music
```

### Get Single Track
```http
GET /music/{track_id}
```

### Create Track (JSON)
```http
POST /music
Content-Type: application/json

{
  "id": "my-track",
  "name": "My Track",
  "title": "My Track",
  "artist": "Artist Name",
  "url": "/pachinball/music/my-track.mp3",
  "duration": 180,
  "map_id": "my-map",
  "tags": ["electronic"]
}
```

### Upload Music File
```http
POST /pachinball/upload/music
Content-Type: multipart/form-data

file: <binary mp3/ogg/wav>
track_id: my-track
title: My Track
artist: Artist Name
map_id: my-map (optional)
```

### Update Track
```http
PUT /music/{track_id}
Content-Type: application/json

{ ... track config ... }
```

### Delete Track
```http
DELETE /music/{track_id}
```

## Backbox Media

### Get Manifest
```http
GET /backbox
```

### Upload Backbox File
```http
POST /pachinball/upload/backbox
Content-Type: multipart/form-data

file: <binary mp4/png>
state: attract|jackpot|fever|reach|adventure
file_type: video|image
```

## Zone Videos

### Get Manifest
```http
GET /zones
```

### Upload Zone Video
```http
POST /pachinball/upload/zone
Content-Type: multipart/form-data

file: <binary mp4/webm>
zone_id: neon-helix
zone_name: Neon Helix
```

## Static Files

### Serve Any File
```http
GET /files/{path}
```
Example: `/api/files/pachinball/music/neon-helix.mp3`

## Health Check

```http
GET /health
```

## Storage Layout on VPS

```
/data/files/pachinball/
├── maps/
│   └── maps.json          # Managed via API
├── music/
│   ├── tracks.json        # Managed via API
│   └── *.mp3              # Uploaded via API
├── backbox/
│   ├── manifest.json      # Auto-generated
│   └── *.mp4, *.png       # Uploaded via API
├── zones/
│   ├── manifest.json      # Auto-updated on upload
│   └── *.mp4              # Uploaded via API
└── shaders/
    └── *.glsl             # Upload manually or via API
```

## Admin Workflow

1. **Add a new map**: POST to `/maps` with map config
2. **Upload music**: POST to `/upload/music` with MP3
3. **Upload backbox video**: POST to `/upload/backbox` 
4. **Upload zone video**: POST to `/upload/zone`

All changes are persisted to JSON files on disk - no repo changes needed.
