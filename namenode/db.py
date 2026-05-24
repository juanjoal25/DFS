"""Capa de acceso a SQLite para el NameNode.

Toda escritura/lectura se serializa con un lock global ya que SQLite no
admite escrituras concurrentes y el alcance del proyecto no lo requiere.
"""
import sqlite3
import threading
import time
from typing import List, Optional, Dict

from common import config

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directories (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner  TEXT NOT NULL,
    path   TEXT NOT NULL,
    UNIQUE(owner, path)
);

CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    path        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    block_size  INTEGER NOT NULL,
    committed   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    UNIQUE(owner, path)
);

CREATE TABLE IF NOT EXISTS blocks (
    block_id  TEXT PRIMARY KEY,
    file_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    size      INTEGER NOT NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS block_locations (
    block_id      TEXT NOT NULL,
    datanode_id   TEXT NOT NULL,
    PRIMARY KEY(block_id, datanode_id)
);

CREATE TABLE IF NOT EXISTS datanodes (
    id                TEXT PRIMARY KEY,
    url               TEXT NOT NULL,
    free_space_bytes  INTEGER NOT NULL DEFAULT 0,
    last_heartbeat    REAL NOT NULL DEFAULT 0
);
"""


def init():
    global _conn
    with _lock:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
        # Directorio raíz por defecto se crea por usuario al hacer login/seed.


def conn() -> sqlite3.Connection:
    assert _conn is not None, "db.init() no fue llamado"
    return _conn


# ----------------- Usuarios -----------------
def get_user(username: str) -> Optional[sqlite3.Row]:
    with _lock:
        cur = conn().execute("SELECT * FROM users WHERE username=?", (username,))
        return cur.fetchone()


def create_user(username: str, password_hash: str):
    with _lock:
        c = conn()
        c.execute(
            "INSERT OR IGNORE INTO users(username, password_hash) VALUES(?,?)",
            (username, password_hash),
        )
        # Asegurar directorio raíz del usuario.
        c.execute(
            "INSERT OR IGNORE INTO directories(owner, path) VALUES(?, '/')",
            (username,),
        )
        c.commit()


# ----------------- Directorios -----------------
def dir_exists(owner: str, path: str) -> bool:
    with _lock:
        cur = conn().execute(
            "SELECT 1 FROM directories WHERE owner=? AND path=?", (owner, path)
        )
        return cur.fetchone() is not None


def mkdir(owner: str, path: str):
    with _lock:
        conn().execute(
            "INSERT INTO directories(owner, path) VALUES(?,?)", (owner, path)
        )
        conn().commit()


def rmdir(owner: str, path: str):
    with _lock:
        conn().execute(
            "DELETE FROM directories WHERE owner=? AND path=?", (owner, path)
        )
        conn().commit()


def dir_is_empty(owner: str, path: str) -> bool:
    prefix = path.rstrip("/") + "/"
    with _lock:
        c = conn()
        f = c.execute(
            "SELECT 1 FROM files WHERE owner=? AND path LIKE ? LIMIT 1",
            (owner, prefix + "%"),
        ).fetchone()
        if f:
            return False
        d = c.execute(
            "SELECT 1 FROM directories WHERE owner=? AND path LIKE ? AND path<>? LIMIT 1",
            (owner, prefix + "%", path),
        ).fetchone()
        return d is None


def list_dir(owner: str, path: str):
    """Devuelve (subdirs, files) que cuelgan directamente de `path`."""
    prefix = "/" if path == "/" else path.rstrip("/") + "/"
    with _lock:
        c = conn()
        dirs = c.execute(
            "SELECT path FROM directories WHERE owner=? AND path LIKE ? AND path<>?",
            (owner, prefix + "%", path),
        ).fetchall()
        files = c.execute(
            "SELECT path, size FROM files WHERE owner=? AND committed=1 AND path LIKE ?",
            (owner, prefix + "%"),
        ).fetchall()

    def direct_child(full: str):
        rest = full[len(prefix):]
        if not rest or "/" in rest:
            return None
        return rest

    sub = []
    for d in dirs:
        name = direct_child(d["path"])
        if name:
            sub.append(name)
    fl = []
    for f in files:
        name = direct_child(f["path"])
        if name:
            fl.append((name, f["size"]))
    return sub, fl


# ----------------- Archivos / bloques -----------------
def create_file(file_id: str, owner: str, path: str, size: int, block_size: int):
    with _lock:
        c = conn()
        # Reemplaza archivo previo en la misma ruta (WORM a nivel de archivo completo).
        old = c.execute(
            "SELECT id FROM files WHERE owner=? AND path=?", (owner, path)
        ).fetchone()
        if old:
            delete_file_by_id(old["id"], _locked=True)
        c.execute(
            "INSERT INTO files(id, owner, path, size, block_size, committed, created_at)"
            " VALUES(?,?,?,?,?,0,?)",
            (file_id, owner, path, size, block_size, time.time()),
        )
        c.commit()


def add_block(block_id: str, file_id: str, seq: int, size: int):
    with _lock:
        conn().execute(
            "INSERT INTO blocks(block_id, file_id, seq, size) VALUES(?,?,?,?)",
            (block_id, file_id, seq, size),
        )
        conn().commit()


def add_block_location(block_id: str, datanode_id: str):
    with _lock:
        conn().execute(
            "INSERT OR IGNORE INTO block_locations(block_id, datanode_id) VALUES(?,?)",
            (block_id, datanode_id),
        )
        conn().commit()


def remove_block_location(block_id: str, datanode_id: str):
    with _lock:
        conn().execute(
            "DELETE FROM block_locations WHERE block_id=? AND datanode_id=?",
            (block_id, datanode_id),
        )
        conn().commit()


def commit_file(file_id: str):
    with _lock:
        conn().execute("UPDATE files SET committed=1 WHERE id=?", (file_id,))
        conn().commit()


def get_file(owner: str, path: str) -> Optional[sqlite3.Row]:
    with _lock:
        return conn().execute(
            "SELECT * FROM files WHERE owner=? AND path=? AND committed=1",
            (owner, path),
        ).fetchone()


def get_blocks(file_id: str):
    with _lock:
        return conn().execute(
            "SELECT * FROM blocks WHERE file_id=? ORDER BY seq", (file_id,)
        ).fetchall()


def get_block_datanodes(block_id: str) -> List[str]:
    with _lock:
        rows = conn().execute(
            "SELECT datanode_id FROM block_locations WHERE block_id=?", (block_id,)
        ).fetchall()
        return [r["datanode_id"] for r in rows]


def delete_file_by_id(file_id: str, _locked: bool = False):
    def _do():
        c = conn()
        block_rows = c.execute(
            "SELECT block_id FROM blocks WHERE file_id=?", (file_id,)
        ).fetchall()
        for b in block_rows:
            c.execute("DELETE FROM block_locations WHERE block_id=?", (b["block_id"],))
        c.execute("DELETE FROM blocks WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
        c.commit()
    if _locked:
        _do()
    else:
        with _lock:
            _do()


def get_file_block_ids(file_id: str) -> List[str]:
    with _lock:
        rows = conn().execute(
            "SELECT block_id FROM blocks WHERE file_id=?", (file_id,)
        ).fetchall()
        return [r["block_id"] for r in rows]


# ----------------- DataNodes -----------------
def upsert_datanode(datanode_id: str, url: str, free_space: int, ts: float):
    with _lock:
        conn().execute(
            "INSERT INTO datanodes(id, url, free_space_bytes, last_heartbeat)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET url=?, free_space_bytes=?, last_heartbeat=?",
            (datanode_id, url, free_space, ts, url, free_space, ts),
        )
        conn().commit()


def get_datanode(datanode_id: str) -> Optional[sqlite3.Row]:
    with _lock:
        return conn().execute(
            "SELECT * FROM datanodes WHERE id=?", (datanode_id,)
        ).fetchone()


def all_datanodes() -> List[sqlite3.Row]:
    with _lock:
        return conn().execute("SELECT * FROM datanodes").fetchall()


def live_datanodes(now: float) -> List[sqlite3.Row]:
    cutoff = now - config.HEARTBEAT_TIMEOUT
    with _lock:
        return conn().execute(
            "SELECT * FROM datanodes WHERE last_heartbeat >= ?"
            " ORDER BY free_space_bytes DESC",
            (cutoff,),
        ).fetchall()


def reconcile_block_locations(datanode_id: str, block_ids: List[str]):
    """Sincroniza las ubicaciones de bloques reportadas por un DataNode."""
    with _lock:
        c = conn()
        c.execute("DELETE FROM block_locations WHERE datanode_id=?", (datanode_id,))
        # Solo registramos bloques que el NameNode conoce.
        for bid in block_ids:
            known = c.execute(
                "SELECT 1 FROM blocks WHERE block_id=?", (bid,)
            ).fetchone()
            if known:
                c.execute(
                    "INSERT OR IGNORE INTO block_locations(block_id, datanode_id)"
                    " VALUES(?,?)",
                    (bid, datanode_id),
                )
        c.commit()


def url_for_datanode(datanode_id: str) -> Optional[str]:
    dn = get_datanode(datanode_id)
    return dn["url"] if dn else None
