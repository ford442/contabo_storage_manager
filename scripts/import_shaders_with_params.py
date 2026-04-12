#!/usr/bin/env python3
"""
Import shaders from image_video_effects to the VPS storage API WITH parameters.

This enhanced version:
1. Reads WGSL shader code from the source
2. ALSO reads parameter definitions from local shader-lists/*.json files
3. Uploads both code AND params to the VPS

Usage:
    # Import all shaders with their params
    python import_shaders_with_params.py
    
    # Import specific shaders
    python import_shaders_with_params.py --shaders liquid,vortex,kaleidoscope
    
    # Dry run (show what would be imported)
    python import_shaders_with_params.py --dry-run
"""

import json
import re
import sys
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Any

# Configuration
SOURCE_BASE = "http://test.1ink.us/image_video_effects/shaders"
SHADER_LISTS_DIR = Path("/root/image_video_effects/public/shader-lists")
VPS_API = "https://storage.noahcohn.com/api/shaders"
VPS_HEALTH = "https://storage.noahcohn.com/health"


def http_get(url: str) -> str:
    """Make a GET request and return response text."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode('utf-8')


def http_post(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Make a POST request with JSON data."""
    json_data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=json_data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def http_put(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Make a PUT request with JSON data."""
    json_data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=json_data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        },
        method='PUT'
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def load_local_shader_params() -> Dict[str, List[Dict[str, Any]]]:
    """Load shader parameter definitions from local JSON files."""
    params_map = {}
    
    if not SHADER_LISTS_DIR.exists():
        print(f"Warning: Shader lists directory not found: {SHADER_LISTS_DIR}")
        return params_map
    
    for json_file in SHADER_LISTS_DIR.glob("*.json"):
        try:
            with open(json_file, "r") as f:
                shader_list = json.load(f)
            
            for shader_data in shader_list:
                shader_id = shader_data.get("id")
                if shader_id and "params" in shader_data:
                    # Convert to API format
                    api_params = []
                    for p in shader_data["params"]:
                        api_params.append({
                            "name": p.get("id", f"param{len(api_params)+1}"),
                            "label": p.get("name", p.get("label", f"Parameter {len(api_params)+1}")),
                            "default": p.get("default", 0.5),
                            "min": p.get("min", 0.0),
                            "max": p.get("max", 1.0),
                            "step": p.get("step", 0.01),
                            "description": p.get("description", ""),
                        })
                    params_map[shader_id] = api_params
        except Exception as e:
            print(f"Warning: Failed to load {json_file}: {e}")
    
    return params_map


def fetch_shader_list() -> List[str]:
    """Fetch the list of shader files from the source."""
    print(f"Fetching shader list from {SOURCE_BASE}...")
    
    html = http_get(SOURCE_BASE + "/")
    
    # Extract .wgsl file links
    pattern = r'href="([^"]+\.wgsl)"'
    matches = re.findall(pattern, html)
    
    print(f"Found {len(matches)} shader files")
    return matches


def fetch_shader_content(filename: str) -> str:
    """Fetch the WGSL content of a shader."""
    url = f"{SOURCE_BASE}/{filename}"
    return http_get(url)


def shader_exists(shader_id: str) -> bool:
    """Check if a shader already exists on the VPS."""
    try:
        url = f"{VPS_API}/{shader_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except Exception:
        return False


def create_or_update_shader(shader_id: str, name: str, wgsl_code: str, 
                            params: Optional[List[Dict]] = None,
                            dry_run: bool = False) -> bool:
    """Create a new shader or update existing one with params."""
    exists = shader_exists(shader_id)
    
    if dry_run:
        action = "Update" if exists else "Create"
        print(f"  [DRY RUN] Would {action}: {shader_id}")
        if params:
            print(f"    With {len(params)} params:")
            for p in params:
                print(f"      - {p['name']}: default={p['default']}")
        return True
    
    if exists:
        # Update existing shader with params
        if params:
            try:
                http_put(f"{VPS_API}/{shader_id}", {"params": params})
                print(f"  ✓ Updated params for {shader_id}")
                return True
            except Exception as e:
                print(f"  ✗ Failed to update params for {shader_id}: {e}")
                return False
        else:
            print(f"  ⊘ {shader_id} exists, no params to update")
            return True
    else:
        # Create new shader
        payload = {
            "id": shader_id,
            "name": name,
            "wgsl_code": wgsl_code,
            "format": "wgsl",
            "source": "import",
        }
        if params:
            payload["params"] = params
        
        try:
            http_post(VPS_API, payload)
            print(f"  ✓ Created {shader_id}" + (f" with {len(params)} params" if params else ""))
            return True
        except Exception as e:
            print(f"  ✗ Failed to create {shader_id}: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description="Import shaders with params to VPS")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported")
    parser.add_argument("--shaders", type=str, help="Comma-separated list of shader IDs to import")
    parser.add_argument("--skip-existing", action="store_true", help="Skip shaders that already exist")
    args = parser.parse_args()
    
    # Check VPS health
    print(f"Checking VPS health at {VPS_HEALTH}...")
    try:
        response = http_get(VPS_HEALTH)
        print(f"VPS status: {response}")
    except Exception as e:
        print(f"Warning: Could not check VPS health: {e}")
    
    # Load local shader params
    print(f"\nLoading shader params from {SHADER_LISTS_DIR}...")
    local_params = load_local_shader_params()
    print(f"Found params for {len(local_params)} shaders")
    
    # Get shader list
    shaders = fetch_shader_list()
    
    if not shaders:
        print("No shaders found!")
        return 1
    
    # Filter if specific shaders requested
    if args.shaders:
        shader_ids = [s.strip() for s in args.shaders.split(",")]
        shaders = [s for s in shaders if Path(s).stem in shader_ids]
        print(f"\nFiltered to {len(shaders)} shaders")
    
    print(f"\n{'='*60}")
    print(f"Starting import of {len(shaders)} shaders...")
    print(f"{'='*60}")
    
    stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    
    for i, shader_file in enumerate(shaders, 1):
        shader_name = Path(shader_file).stem
        
        # Skip if --skip-existing and shader exists
        if args.skip_existing and shader_exists(shader_name):
            print(f"[{i:3d}/{len(shaders)}] ⊘ {shader_name} (exists, skipped)")
            stats["skipped"] += 1
            continue
        
        try:
            # Fetch WGSL content
            wgsl_code = fetch_shader_content(shader_file)
            
            # Get params if available
            params = local_params.get(shader_name)
            if params:
                print(f"[{i:3d}/{len(shaders)}] Processing {shader_name} with {len(params)} params...")
            else:
                print(f"[{i:3d}/{len(shaders)}] Processing {shader_name} (no params found)...")
            
            # Create/update shader
            exists = shader_exists(shader_name)
            success = create_or_update_shader(
                shader_id=shader_name,
                name=shader_name,
                wgsl_code=wgsl_code,
                params=params,
                dry_run=args.dry_run
            )
            
            if success:
                if exists:
                    stats["updated"] += 1
                else:
                    stats["created"] += 1
            else:
                stats["failed"] += 1
                
        except Exception as e:
            print(f"[{i:3d}/{len(shaders)}] ✗ {shader_name} - {e}")
            stats["failed"] += 1
    
    print(f"{'='*60}")
    print(f"\nImport complete!")
    print(f"  Created:  {stats['created']}")
    print(f"  Updated:  {stats['updated']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")
    print(f"  Total:    {len(shaders)}")
    
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
