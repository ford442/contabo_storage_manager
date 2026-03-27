#!/bin/bash
# Check if VPS has the latest deployment

echo "Checking VPS deployment status..."
echo "================================"

# Get local commit
LOCAL_COMMIT=$(git rev-parse --short HEAD)
echo "Local commit: $LOCAL_COMMIT"

# Get remote commit (GitHub)
REMOTE_COMMIT=$(curl -s https://api.github.com/repos/ford442/contabo_storage_manager/commits/main | jq -r '.sha[:7]')
echo "GitHub commit: $REMOTE_COMMIT"

# Check if VPS is up to date
echo ""
echo "Checking VPS endpoints..."
HEALTH=$(curl -s https://storage.noahcohn.com/health)
echo "Health: $HEALTH"

# Check if POST endpoint exists (this will fail on old deployments)
POST_CHECK=$(curl -s -X POST https://storage.noahcohn.com/api/shaders -H "Content-Type: application/json" -d '{"id":"test"}' -w "%{http_code}" -o /dev/null)
echo "POST /api/shaders: HTTP $POST_CHECK"

if [ "$POST_CHECK" = "405" ]; then
    echo ""
    echo "⚠️  VPS is running OLD code (POST not supported)"
    echo "Run ./restart-vps.sh on the VPS to update"
elif [ "$POST_CHECK" = "422" ] || [ "$POST_CHECK" = "200" ]; then
    echo ""
    echo "✅ VPS is running NEW code (POST supported)"
else
    echo ""
    echo "? Unknown status code: $POST_CHECK"
fi
