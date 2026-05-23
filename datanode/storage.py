"""Almacenamiento de bloques en disco local."""
import os
import shutil
from typing import List

from common import config

_BLOCK_DIR = config.DATA_DIR


def init():
    os.makedirs(_BLOCK_DIR, exist_ok=True)


def _path(block_id: str) -> str:
    # block_id es un uuid4; evitar separadores de ruta por seguridad.
    safe = os.path.basename(block_id)
    return os.path.join(_BLOCK_DIR, safe)


def write_block(block_id: str, data: bytes):
    with open(_path(block_id), "wb") as f:
        f.write(data)


def read_block(block_id: str) -> bytes:
    with open(_path(block_id), "rb") as f:
        return f.read()


def exists(block_id: str) -> bool:
    return os.path.exists(_path(block_id))


def delete_block(block_id: str) -> bool:
    p = _path(block_id)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def list_blocks() -> List[str]:
    if not os.path.isdir(_BLOCK_DIR):
        return []
    return [
        f for f in os.listdir(_BLOCK_DIR)
        if os.path.isfile(os.path.join(_BLOCK_DIR, f))
    ]


def free_space_bytes() -> int:
    if config.DATANODE_CAPACITY_BYTES > 0:
        used = sum(
            os.path.getsize(os.path.join(_BLOCK_DIR, f)) for f in list_blocks()
        )
        return max(0, config.DATANODE_CAPACITY_BYTES - used)
    usage = shutil.disk_usage(_BLOCK_DIR)
    return usage.free
