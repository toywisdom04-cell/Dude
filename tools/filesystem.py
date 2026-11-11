"""Filesystem tools: list, search, read, write, create, rename, move, copy.

Deletion is heavily guarded (permission gate + a confirmation requirement).
"""
import os
import shutil
from pathlib import Path

from .registry import ToolResult, register_tool


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path))).resolve()


@register_tool("list_directory", "List files and folders in a directory", permission="allow")
def list_directory(path: str = ".") -> ToolResult:
    try:
        entries = [p.name for p in _expand(path).iterdir()]
        return ToolResult(True, f"{len(entries)} entries in {path}", data=entries)
    except OSError as exc:
        return ToolResult(False, f"Cannot list {path}: {exc}")


@register_tool("search_files", "Search for files by name pattern in a directory", permission="allow")
def search_files(pattern: str, path: str = ".") -> ToolResult:
    try:
        root = _expand(path)
        matches = [str(p) for p in root.rglob(pattern)][:100]
        return ToolResult(True, f"Found {len(matches)} matches", data=matches)
    except OSError as exc:
        return ToolResult(False, f"Search failed: {exc}")


@register_tool("read_file", "Read a text file's contents", permission="allow")
def read_file(path: str, max_lines: int = 500) -> ToolResult:
    try:
        lines = _expand(path).read_text(encoding="utf-8", errors="replace").splitlines()
        content = "\n".join(lines[:max_lines])
        return ToolResult(True, f"Read {path}", data=content)
    except OSError as exc:
        return ToolResult(False, f"Cannot read {path}: {exc}")


@register_tool("write_file", "Write text content to a file", permission="ask")
def write_file(path: str, content: str) -> ToolResult:
    try:
        target = _expand(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {path}")
    except OSError as exc:
        return ToolResult(False, f"Cannot write {path}: {exc}")


@register_tool("create_file", "Create an empty file", permission="ask")
def create_file(path: str) -> ToolResult:
    try:
        target = _expand(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        return ToolResult(True, f"Created {path}")
    except OSError as exc:
        return ToolResult(False, f"Cannot create {path}: {exc}")


@register_tool("rename", "Rename a file or folder", permission="ask")
def rename(old_path: str, new_name: str) -> ToolResult:
    try:
        src = _expand(old_path)
        src.rename(src.with_name(new_name))
        return ToolResult(True, f"Renamed to {new_name}")
    except OSError as exc:
        return ToolResult(False, f"Rename failed: {exc}")


@register_tool("move", "Move a file or folder", permission="ask")
def move(source: str, destination: str) -> ToolResult:
    try:
        shutil.move(str(_expand(source)), str(_expand(destination)))
        return ToolResult(True, f"Moved {source} to {destination}")
    except OSError as exc:
        return ToolResult(False, f"Move failed: {exc}")


@register_tool("copy", "Copy a file or folder", permission="ask")
def copy(source: str, destination: str) -> ToolResult:
    try:
        src = _expand(source)
        dst = _expand(destination)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        return ToolResult(True, f"Copied to {destination}")
    except OSError as exc:
        return ToolResult(False, f"Copy failed: {exc}")


@register_tool("delete", "Delete a file or folder (always confirmed)", permission="ask")
def delete(path: str) -> ToolResult:
    # Guard: the permission gate already asks; require the path to be explicit
    # and refuse root-level or home-root deletions.
    target = _expand(path)
    protected = {str(Path.home()), str(Path("/")), str(Path.home().anchor)}
    if str(target) in protected or target == Path.home():
        return ToolResult(False, "Refusing to delete a protected path.")
    try:
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        return ToolResult(True, f"Deleted {path}")
    except OSError as exc:
        return ToolResult(False, f"Delete failed: {exc}")


@register_tool("open_folder", "Open a folder in the file manager", permission="allow")
def open_folder(path: str) -> ToolResult:
    from ..platform.registry import get_platform
    ok = get_platform().open_in_file_manager(path)
    return ToolResult(ok, f"Opened {path}" if ok else f"Could not open {path}")

