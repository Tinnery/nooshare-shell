"""Uvicorn entry point for the dashboard server."""
import argparse
import uvicorn

from .config import HOST, PORT


def main():
    """Run the dashboard server."""
    parser = argparse.ArgumentParser(description="NooShare Dashboard Server")
    parser.add_argument("--host", default=HOST, help=f"Host to bind (default: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port to bind (default: {PORT})")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()
    
    uvicorn.run("dashboard.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
