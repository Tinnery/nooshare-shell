"""Dashboard configuration — paths and server settings."""
from pathlib import Path

# Package structure
PACKAGE_DIR = Path(__file__).parent
DASHBOARD_ROOT = PACKAGE_DIR.parent
REPO_ROOT = DASHBOARD_ROOT.parent.parent  # packages/dashboard -> packages -> repo root

# Knowledge directory
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"

# Frontend build output
FRONTEND_DIST = DASHBOARD_ROOT / "frontend" / "dist"

# Server settings
HOST = "0.0.0.0"
PORT = 8000

# Knowledge file settings
SUPPORTED_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".toml"}
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB
