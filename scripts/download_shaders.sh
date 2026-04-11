#!/bin/bash
# Download all shaders from test.1ink.us to the VPS

set -e

SOURCE_URL="http://test.1ink.us/image_video_effects/shaders"
DEST_DIR="/home/ftpbridge/files/image-effects/shaders"

echo "Downloading shaders from $SOURCE_URL to $DEST_DIR..."
echo "======================================================"

# Create destination directory
mkdir -p "$DEST_DIR"

# Get list of shader files
echo "Fetching shader list..."
SHADER_LIST=$(curl -sL "$SOURCE_URL/" | grep -oP 'href="[^"]+\.wgsl"' | sed 's/href="//;s/"$//')

TOTAL=$(echo "$SHADER_LIST" | wc -l)
echo "Found $TOTAL shader files"
echo ""

# Download each shader
COUNTER=0
for shader in $SHADER_LIST; do
    COUNTER=$((COUNTER + 1))
    shader_name=$(basename "$shader")
    
    # Create a JSON wrapper for each shader
    json_file="${shader_name%.wgsl}.json"
    
    echo "[$COUNTER/$TOTAL] Processing: $shader_name"
    
    # Download the WGSL content
    wgsl_content=$(curl -sL "$SOURCE_URL/$shader")
    
    # Escape the WGSL content for JSON
    escaped_content=$(echo "$wgsl_content" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
    
    # Create JSON structure
    cat > "$DEST_DIR/$json_file" << EOF
{
  "id": "${shader_name%.wgsl}",
  "name": "${shader_name%.wgsl}",
  "author": "",
  "date": "$(date -u +%Y-%m-%dT%H:%M:%S)",
  "type": "shader",
  "description": "",
  "filename": "$json_file",
  "tags": [],
  "rating": null,
  "source": "import",
  "original_id": "${shader_name%.wgsl}",
  "format": "wgsl",
  "converted": false,
  "has_errors": false,
  "wgsl_code": $escaped_content
}
EOF
    
    # Also save the raw WGSL file
    echo "$wgsl_content" > "$DEST_DIR/$shader_name"
done

echo ""
echo "======================================================"
echo "Downloaded $COUNTER shaders to $DEST_DIR"
echo ""
ls -la "$DEST_DIR" | tail -10
