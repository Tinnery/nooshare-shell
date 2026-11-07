"""FastAPI application — API routes and frontend hosting."""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIST
from .knowledge import build_tree, read_content, get_meta, _tree_to_dict

app = FastAPI(title="NooShare Dashboard", version="0.1.0")


@app.get("/api/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/knowledge/tree")
def knowledge_tree():
    """Get the knowledge directory tree."""
    tree = build_tree()
    return _tree_to_dict(tree)


@app.get("/api/knowledge/content/{path:path}")
def knowledge_content(path: str):
    """Get the content of a knowledge file."""
    try:
        return read_content(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/knowledge/meta/{path:path}")
def knowledge_meta(path: str):
    """Get metadata for a knowledge file."""
    try:
        return get_meta(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Mount frontend static files (must be last)
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
