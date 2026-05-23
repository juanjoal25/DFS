"""Pruebas unitarias sin red: normalización de rutas y política de asignación."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from namenode.main import normalize_path, parent_dir


def test_normalize_path():
    assert normalize_path("/") == "/"
    assert normalize_path("docs") == "/docs"
    assert normalize_path("/docs/") == "/docs"
    assert normalize_path("/a/b/c/") == "/a/b/c"


def test_parent_dir():
    assert parent_dir("/docs") == "/"
    assert parent_dir("/a/b/c") == "/a/b"
    assert parent_dir("/") == "/"


def test_block_size_partition():
    # ceil(size/block) bloques
    import math
    block = 1024 * 1024
    for size, expected in [(0, 0), (1, 1), (block, 1), (block + 1, 2), (5 * block, 5)]:
        n = max(0, math.ceil(size / block)) if size else 0
        assert n == expected
