#!/bin/bash
# Setup script for Contabo Storage Manager systemd services

set -e

echo "=== Contabo Storage Manager Service Setup ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (use sudo)"
    exit 1
fi

# Paths
REPO_DIR="/root/contabo_storage_manager"
PYTHON_DIR="$REPO_DIR/packages/python-bridge"
NODE_DIR="$REPO_DIR/packages/node-bridge"
SERVICE_DIR="$REPO_DIR/systemd"

# Check if repository exists
if [ ! -d "$REPO_DIR" ]; then
    echo "Error: Repository not found at $REPO_DIR"
    exit 1
fi

echo "[1] Installing Python dependencies..."
cd "$PYTHON_DIR"
if [ ! -d "venv" ] && [ ! -d ".venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
echo "    ✓ Python dependencies installed"

echo ""
echo "[2] Installing Node dependencies..."
cd "$NODE_DIR"
if [ ! -d "node_modules" ]; then
    npm install
fi
echo "    ✓ Node dependencies installed"

echo ""
echo "[3] Installing systemd services..."

# Install Python service
cp "$SERVICE_DIR/contabo-storage-python.service" /etc/systemd/system/

# Install Node service
cp "$SERVICE_DIR/contabo-storage-node.service" /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

echo "    ✓ Services installed"

echo ""
echo "[4] Creating models directory..."
mkdir -p /data/files/models
chmod 755 /data/files/models
echo "    ✓ Models directory ready"

echo ""
echo "[5] Testing Python service..."
cd "$PYTHON_DIR"
source venv/bin/activate
python3 -c "from app.main import app; print('    ✓ Python app loads successfully')"
deactivate

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the services:"
echo "  systemctl start contabo-storage-python"
echo "  systemctl start contabo-storage-node"
echo ""
echo "To enable on boot:"
echo "  systemctl enable contabo-storage-python"
echo "  systemctl enable contabo-storage-node"
echo ""
echo "To check status:"
echo "  systemctl status contabo-storage-python"
echo "  systemctl status contabo-storage-node"
echo ""
echo "To view logs:"
echo "  journalctl -u contabo-storage-python -f"
echo "  journalctl -u contabo-storage-node -f"
