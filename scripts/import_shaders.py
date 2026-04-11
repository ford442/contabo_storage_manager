#!/usr/bin/env python3
"""Import shaders from test.1ink.us to the VPS storage API."""

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Configuration
SOURCE_BASE = "http://test.1ink.us/image_video_effects/shaders"
VPS_API = "https://storage.noahcohn.com/api/shaders"
VPS_HEALTH = "https://storage.noahcohn.com/health"


def http_get(url):
    """Make a GET request and return response text."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode('utf-8')


def http_post(url, data):
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


def fetch_shader_list():
    """Fetch the list of shader files from the source."""
    print(f"Fetching shader list from {SOURCE_BASE}...")
    
    html = http_get(SOURCE_BASE + "/")
    
    # Extract .wgsl file links
    pattern = r'href="([^"]+\.wgsl)"'
    matches = re.findall(pattern, html)
    
    print(f"Found {len(matches)} shader files")
    return matches


def fetch_shader_content(filename):
    """Fetch the WGSL content of a shader."""
    url = f"{SOURCE_BASE}/{filename}"
    return http_get(url)


def upload_to_vps(shader_id, name, wgsl_code):
    """Upload a shader to the VPS via API."""
    payload = {
        "id": shader_id,
        "name": name,
        "wgsl_code": wgsl_code,
        "format": "wgsl",
        "source": "import",
    }
    
    return http_post(VPS_API, payload)


def main():
    # Check VPS health
    print(f"Checking VPS health at {VPS_HEALTH}...")
    try:
        response = http_get(VPS_HEALTH)
        print(f"VPS status: {response}")
    except Exception as e:
        print(f"Warning: Could not check VPS health: {e}")
    
    # Get shader list
    shaders = fetch_shader_list()
    
    if not shaders:
        print("No shaders found!")
        return 1
    
    print(f"\nStarting import of {len(shaders)} shaders...")
    print("=" * 60)
    
    success = 0
    failed = 0
    
    for i, shader_file in enumerate(shaders, 1):
        shader_name = Path(shader_file).stem
        
        try:
            # Fetch WGSL content
            wgsl_code = fetch_shader_content(shader_file)
            
            # Upload to VPS
            result = upload_to_vps(shader_name, shader_name, wgsl_code)
            
            print(f"[{i:3d}/{len(shaders)}] ✓ {shader_name}")
            success += 1
            
        except Exception as e:
            print(f"[{i:3d}/{len(shaders)}] ✗ {shader_name} - {e}")
            failed += 1
    
    print("=" * 60)
    print(f"\nImport complete!")
    print(f"  Success: {success}")
    print(f"  Failed:  {failed}")
    print(f"  Total:   {len(shaders)}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
