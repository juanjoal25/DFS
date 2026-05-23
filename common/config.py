"""Carga de configuración desde variables de entorno."""
import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- Comunes ---
BLOCK_SIZE_MB = _int_env("BLOCK_SIZE_MB", 64)
BLOCK_SIZE_BYTES = BLOCK_SIZE_MB * 1024 * 1024
REPLICATION_FACTOR = _int_env("REPLICATION_FACTOR", 2)
HEARTBEAT_INTERVAL = _int_env("HEARTBEAT_INTERVAL", 5)
HEARTBEAT_TIMEOUT = _int_env("HEARTBEAT_TIMEOUT", 15)

# --- NameNode ---
NAMENODE_URL = os.environ.get("NAMENODE_URL", "http://localhost:8000")
DB_PATH = os.environ.get("DB_PATH", "namenode.db")
JWT_SECRET = os.environ.get("JWT_SECRET", "dfs-dev-secret-change-me")
JWT_EXPIRES_SECONDS = _int_env("JWT_EXPIRES_SECONDS", 3600)
# Usuarios semilla: "user1:pass1,user2:pass2"
SEED_USERS = os.environ.get("SEED_USERS", "admin:admin")

# --- DataNode ---
DATANODE_ID = os.environ.get("DATANODE_ID", "datanode-1")
DATA_DIR = os.environ.get("DATA_DIR", "./data/blocks")
# URL pública con la que el NameNode/cliente alcanzan a este DataNode.
DATANODE_PUBLIC_URL = os.environ.get("DATANODE_PUBLIC_URL", "http://localhost:8100")
DATANODE_PORT = _int_env("DATANODE_PORT", 8100)
# Cuota simulada de disco en bytes (opcional). 0 = usar disco real.
DATANODE_CAPACITY_BYTES = int(os.environ.get("DATANODE_CAPACITY_BYTES", "0"))
