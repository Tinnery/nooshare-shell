"""Knowledge directory reader and Markdown renderer."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import frontmatter
from markdown_it import MarkdownIt

from .config import KNOWLEDGE_DIR, SUPPORTED_EXTENSIONS, MAX_FILE_SIZE


@dataclass
class FileNode:
    """Represents a file in the knowledge tree."""
    name: str
    path: str
    extension: str
    size: int
    modified: float


@dataclass
class DirNode:
    """Represents a directory in the knowledge tree."""
    name: str
    path: str
    children: list = field(default_factory=list)


def build_tree(base_path: Optional[Path] = None) -> DirNode:
    """Build a tree structure of the knowledge directory."""
    base = base_path or KNOWLEDGE_DIR
    if not base.exists():
        return DirNode(name="knowledge", path="")
    
    def _scan(path: Path, relative: str = "") -> DirNode:
        node = DirNode(name=path.name, path=relative)
        
        for item in sorted(path.iterdir()):
            if item.name.startswith("."):
                continue
            
            item_rel = f"{relative}/{item.name}" if relative else item.name
            
            if item.is_dir():
                node.children.append(_scan(item, item_rel))
            elif item.is_file() and item.suffix in SUPPORTED_EXTENSIONS:
                stat = item.stat()
                node.children.append(FileNode(
                    name=item.name,
                    path=item_rel,
                    extension=item.suffix,
                    size=stat.st_size,
                    modified=stat.st_mtime
                ))
        
        return node
    
    return _scan(base)


def read_content(file_path: str) -> dict:
    """Read and parse a knowledge file."""
    full_path = KNOWLEDGE_DIR / file_path
    
    # Security check: prevent path traversal
    try:
        full_path.resolve().relative_to(KNOWLEDGE_DIR.resolve())
    except ValueError:
        raise ValueError("Invalid path")
    
    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    if full_path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {file_path}")
    
    content = full_path.read_text(encoding="utf-8")
    result = {
        "path": file_path,
        "name": full_path.name,
        "extension": full_path.suffix,
        "raw": content,
    }
    
    # Parse Markdown with frontmatter
    if full_path.suffix == ".md":
        post = frontmatter.loads(content)
        result["frontmatter"] = dict(post.metadata)
        result["html"] = MarkdownIt().render(post.content)
    
    return result


def get_meta(file_path: str) -> dict:
    """Get metadata for a knowledge file."""
    full_path = KNOWLEDGE_DIR / file_path
    
    try:
        full_path.resolve().relative_to(KNOWLEDGE_DIR.resolve())
    except ValueError:
        raise ValueError("Invalid path")
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    stat = full_path.stat()
    return {
        "path": file_path,
        "name": full_path.name,
        "extension": full_path.suffix,
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }


def _tree_to_dict(node) -> dict:
    """Convert tree nodes to JSON-serializable dict."""
    if isinstance(node, FileNode):
        return {
            "type": "file",
            "name": node.name,
            "path": node.path,
            "extension": node.extension,
            "size": node.size,
            "modified": node.modified,
        }
    elif isinstance(node, DirNode):
        return {
            "type": "directory",
            "name": node.name,
            "path": node.path,
            "children": [_tree_to_dict(child) for child in node.children],
        }
