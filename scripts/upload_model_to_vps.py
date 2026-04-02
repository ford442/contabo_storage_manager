#!/usr/bin/env python3
"""
Model Upload Script for Contabo Storage Manager VPS

Uploads WebLLM-compatible models to storage.noahcohn.com for reliable
range-header supported downloads.

Usage:
    python upload_model_to_vps.py --model-id "Llama-2-7b-chat-hf-q4f32_1-MLC" \
                                  --source "huggingface" \
                                  --hf-repo "mlc-ai/Llama-2-7b-chat-hf-q4f32_1-MLC"

    python upload_model_to_vps.py --model-id "vicuna-7b-q4f32-webllm" \
                                  --source "local" \
                                  --local-path "/path/to/model/files"
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    logger.warning("requests not installed. HTTP downloads won't work.")

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    logger.warning("paramiko not installed. SCP uploads won't work.")

try:
    from huggingface_hub import hf_hub_download, list_repo_files, HfApi
    HAS_HF = True
except ImportError:
    HAS_HF = False
    logger.warning("huggingface_hub not installed. HF downloads won't work.")


# VPS Configuration
VPS_HOST = os.getenv("VPS_HOST", "storage.noahcohn.com")
VPS_USER = os.getenv("VPS_USER", "root")
VPS_KEY_PATH = os.getenv("VPS_KEY_PATH", "~/.ssh/id_rsa")
VPS_MODELS_DIR = os.getenv("VPS_MODELS_DIR", "/data/files/models")
VPS_BASE_URL = f"https://{VPS_HOST}/models"


class ModelUploader:
    """Handles uploading models to the VPS."""
    
    def __init__(self, host: str, user: str, key_path: str, models_dir: str):
        self.host = host
        self.user = user
        self.key_path = os.path.expanduser(key_path)
        self.models_dir = models_dir
        self.ssh_client = None
        self.sftp_client = None
        
    def connect(self):
        """Connect to VPS via SSH/SCP."""
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko is required for SCP uploads")
        
        logger.info(f"Connecting to {self.host} as {self.user}...")
        
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Try key-based auth first
        try:
            self.ssh_client.connect(
                hostname=self.host,
                username=self.user,
                key_filename=self.key_path,
                timeout=30
            )
        except paramiko.AuthenticationException:
            # Fall back to password if available
            password = os.getenv("VPS_PASSWORD")
            if password:
                self.ssh_client.connect(
                    hostname=self.host,
                    username=self.user,
                    password=password,
                    timeout=30
                )
            else:
                raise
        
        self.sftp_client = self.ssh_client.open_sftp()
        logger.info("Connected successfully")
        
    def disconnect(self):
        """Disconnect from VPS."""
        if self.sftp_client:
            self.sftp_client.close()
        if self.ssh_client:
            self.ssh_client.close()
        logger.info("Disconnected")
        
    def ensure_dir(self, path: str):
        """Ensure directory exists on VPS."""
        try:
            self.sftp_client.stat(path)
        except FileNotFoundError:
            logger.info(f"Creating directory: {path}")
            # Create parent directories recursively
            parent = os.path.dirname(path)
            if parent and parent != path:
                self.ensure_dir(parent)
            self.sftp_client.mkdir(path)
            
    def upload_file(self, local_path: Path, remote_path: str):
        """Upload a single file to VPS."""
        logger.info(f"Uploading {local_path.name}...")
        self.sftp_client.put(str(local_path), remote_path)
        
        # Verify upload
        local_size = local_path.stat().st_size
        remote_stat = self.sftp_client.stat(remote_path)
        
        if remote_stat.st_size != local_size:
            raise RuntimeError(f"Size mismatch: local={local_size}, remote={remote_stat.st_size}")
        
        logger.info(f"  ✓ Uploaded ({local_size} bytes)")
        
    def upload_directory(self, local_dir: Path, model_id: str) -> List[str]:
        """Upload a local directory to VPS as a model."""
        remote_dir = f"{self.models_dir}/{model_id}"
        self.ensure_dir(remote_dir)
        
        uploaded_files = []
        
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(local_dir)
                remote_path = f"{remote_dir}/{relative_path}"
                
                # Ensure subdirectory exists
                remote_subdir = os.path.dirname(remote_path)
                self.ensure_dir(remote_subdir)
                
                self.upload_file(file_path, remote_path)
                uploaded_files.append(str(relative_path))
        
        return uploaded_files


def download_from_huggingface(
    repo_id: str,
    local_dir: Path,
    allow_patterns: Optional[List[str]] = None
) -> List[Path]:
    """Download model files from Hugging Face."""
    if not HAS_HF:
        raise RuntimeError("huggingface_hub is required for HF downloads")
    
    logger.info(f"Downloading from Hugging Face: {repo_id}")
    
    local_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    
    # List files in repo
    files = list_repo_files(repo_id)
    
    # Filter files if patterns provided
    if allow_patterns:
        import fnmatch
        filtered_files = []
        for file in files:
            for pattern in allow_patterns:
                if fnmatch.fnmatch(file, pattern):
                    filtered_files.append(file)
                    break
        files = filtered_files
    
    logger.info(f"Found {len(files)} files to download")
    
    for file in files:
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                filename=file,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False
            )
            downloaded_files.append(Path(downloaded))
            logger.info(f"  ✓ {file}")
        except Exception as e:
            logger.error(f"  ✗ {file}: {e}")
    
    return downloaded_files


def verify_range_headers(model_id: str, base_url: str = VPS_BASE_URL) -> bool:
    """Verify that range headers work for uploaded model."""
    if not HAS_REQUESTS:
        logger.warning("requests not available, skipping verification")
        return True
    
    logger.info("Verifying range header support...")
    
    # Get list of files
    try:
        response = requests.get(f"{base_url}/list", timeout=30)
        response.raise_for_status()
        models = response.json().get("models", [])
        
        model_info = None
        for m in models:
            if m["id"] == model_id:
                model_info = m
                break
        
        if not model_info:
            logger.error(f"Model {model_id} not found after upload")
            return False
        
        # Test range request on first binary file
        test_file = None
        for f in model_info["files"]:
            if f["name"].endswith(".bin") or f["name"].endswith(".wasm"):
                test_file = f
                break
        
        if not test_file:
            logger.warning("No binary file found to test range headers")
            return True
        
        file_url = f"{base_url}/{model_id}/{test_file['name']}"
        
        # Test HEAD request
        head_response = requests.head(file_url, timeout=10)
        if "accept-ranges" not in head_response.headers.get("accept-ranges", "").lower():
            logger.warning("Accept-Ranges header not present in HEAD response")
        else:
            logger.info("  ✓ HEAD request supports range headers")
        
        # Test range request
        range_response = requests.get(
            file_url,
            headers={"Range": "bytes=0-1023"},
            timeout=10
        )
        
        if range_response.status_code == 206:
            logger.info("  ✓ Range requests working (206 Partial Content)")
            return True
        else:
            logger.warning(f"Range request returned status {range_response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False


def create_model_metadata(
    model_id: str,
    source: str,
    files: List[str],
    metadata: Optional[Dict] = None
) -> Dict:
    """Create model metadata for WebLLM compatibility."""
    return {
        "model_id": model_id,
        "source": source,
        "base_url": f"{VPS_BASE_URL}/{model_id}",
        "files": files,
        "uploaded_at": json.dumps({"__type__": "datetime", "iso": "now"}),
        "webllm_compatible": True,
        **(metadata or {})
    }


def main():
    parser = argparse.ArgumentParser(
        description="Upload WebLLM models to VPS"
    )
    parser.add_argument(
        "--model-id",
        required=True,
        help="Unique model ID (e.g., 'Llama-2-7b-chat-hf-q4f32_1-MLC')"
    )
    parser.add_argument(
        "--source",
        choices=["huggingface", "local"],
        required=True,
        help="Source of model files"
    )
    parser.add_argument(
        "--hf-repo",
        help="Hugging Face repo ID (for --source huggingface)"
    )
    parser.add_argument(
        "--local-path",
        help="Local directory path (for --source local)"
    )
    parser.add_argument(
        "--temp-dir",
        default="/tmp/model_upload",
        help="Temporary directory for downloads"
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip range header verification"
    )
    parser.add_argument(
        "--vps-host",
        default=VPS_HOST,
        help="VPS hostname"
    )
    parser.add_argument(
        "--vps-user",
        default=VPS_USER,
        help="VPS SSH username"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.source == "huggingface" and not args.hf_repo:
        parser.error("--hf-repo is required when --source is huggingface")
    if args.source == "local" and not args.local_path:
        parser.error("--local-path is required when --source is local")
    
    temp_dir = Path(args.temp_dir) / args.model_id
    
    try:
        # Step 1: Get model files
        if args.source == "huggingface":
            downloaded = download_from_huggingface(
                args.hf_repo,
                temp_dir,
                allow_patterns=["*.bin", "*.json", "*.wasm", "*.model", "*.md"]
            )
            if not downloaded:
                logger.error("No files downloaded")
                return 1
            source_dir = temp_dir
        else:
            source_dir = Path(args.local_path)
            if not source_dir.exists():
                logger.error(f"Local path does not exist: {source_dir}")
                return 1
        
        # Step 2: Upload to VPS
        uploader = ModelUploader(
            host=args.vps_host,
            user=args.vps_user,
            key_path=VPS_KEY_PATH,
            models_dir=VPS_MODELS_DIR
        )
        
        uploader.connect()
        try:
            uploaded_files = uploader.upload_directory(source_dir, args.model_id)
            logger.info(f"Uploaded {len(uploaded_files)} files")
        finally:
            uploader.disconnect()
        
        # Step 3: Verify range headers
        if not args.skip_verify:
            verify_range_headers(args.model_id)
        
        # Step 4: Print summary
        model_url = f"{VPS_BASE_URL}/{args.model_id}"
        logger.info("")
        logger.info("=" * 60)
        logger.info("Upload Complete!")
        logger.info("=" * 60)
        logger.info(f"Model ID: {args.model_id}")
        logger.info(f"Base URL: {model_url}")
        logger.info(f"Config URL: {model_url}/mlc-chat-config.json")
        logger.info("")
        logger.info("Add this to your WebLLM config:")
        logger.info(f'  model: "{model_url}/",')
        logger.info(f'  model_id: "{args.model_id}",')
        logger.info("=" * 60)
        
        return 0
        
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return 1
    finally:
        # Cleanup temp directory
        if args.source == "huggingface" and temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
