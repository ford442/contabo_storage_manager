# Pachinball Content Storage

This directory contains all dynamic content for the Pachinball game.

## Directory Structure

```
pachinball/
├── maps/
│   └── maps.json          # Map configurations (LCD table themes)
├── music/
│   └── tracks.json        # Music track metadata
│   └── *.mp3              # Music files
├── backbox/
│   └── manifest.json      # Backbox media manifest
│   └── *.mp4, *.png       # Attract mode videos and images
├── zones/
│   └── manifest.json      # Zone intro video manifest
│   └── *.mp4              # Zone intro videos
├── shaders/
│   └── *.glsl             # Custom shader files
└── README.md              # This file
```

## Maps JSON Format

```json
{
  "id": "neon-helix",
  "name": "Neon Helix",
  "baseColor": "#00d9ff",
  "accentColor": "#ffffff",
  "glowIntensity": 1.0,
  "backgroundPattern": "hex",
  "animationSpeed": 0.5,
  "musicTrackId": "neon-helix",
  "shaderUrl": "/pachinball/shaders/neon-helix.glsl",
  "mode": "fixed",
  "worldLength": 200
}
```

## Music JSON Format

```json
{
  "id": "neon-helix",
  "name": "Neon Helix",
  "artist": "Pachinball",
  "url": "/pachinball/music/neon-helix.mp3",
  "duration": 180,
  "map_id": "neon-helix",
  "tags": ["electronic", "synthwave"]
}
```

## API Endpoints

- `GET /api/maps` - List all maps
- `GET /api/music` - List all music tracks
- `GET /api/pachinball/backbox` - Get backbox media manifest
- `GET /api/pachinball/zones` - Get zone video manifest
- `GET /api/pachinball/health` - Health check

## Adding New Content

1. **Maps**: Add entry to `maps/maps.json`
2. **Music**: Add entry to `music/tracks.json` and upload MP3 file
3. **Backbox Videos**: Upload to `backbox/` and update `manifest.json`
4. **Zone Videos**: Upload to `zones/` and update `manifest.json`

The game will automatically fetch new content on next load or when refreshing the map list.
