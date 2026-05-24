"""NameNode: API REST de metadatos, namespace, heartbeats y re-replicación."""
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Depends, HTTPException

from common import config
from common.schemas import (
    LoginRequest, LoginResponse,
    UploadPlanRequest, UploadPlanResponse, BlockPlan,
    CommitRequest, CommitResponse,
    FileMapResponse, BlockLocation,
    LsResponse, LsEntry, MkdirRequest, GenericResult,
    HeartbeatPayload,
)
from namenode import db, auth, allocator, replication

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("namenode")


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def parent_dir(path: str) -> str:
    path = normalize_path(path)
    if path == "/":
        return "/"
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    auth.seed_users()
    task = asyncio.create_task(replication.loop())
    log.info("NameNode iniciado. Usuarios semilla cargados.")
    yield
    task.cancel()


app = FastAPI(title="DFS NameNode", lifespan=lifespan)


# ----------------- Auth -----------------
@app.post("/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    token = auth.authenticate(req.username, req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return LoginResponse(token=token, expires_in=config.JWT_EXPIRES_SECONDS)


# ----------------- Upload plan / commit -----------------
@app.post("/files/upload-plan", response_model=UploadPlanResponse)
def upload_plan(req: UploadPlanRequest, user: str = Depends(auth.current_user)):
    path = normalize_path(req.path)
    if not db.dir_exists(user, parent_dir(path)):
        raise HTTPException(
            status_code=400, detail=f"Directorio padre no existe: {parent_dir(path)}"
        )
    if not req.block_ids:
        raise HTTPException(status_code=400, detail="Lista de bloques vacía")

    file_id = str(uuid.uuid4())
    db.create_file(file_id, user, path, req.size, config.BLOCK_SIZE_BYTES)

    # Calcular tamaño de cada bloque.
    sizes = []
    remaining = req.size
    for _ in req.block_ids:
        s = min(config.BLOCK_SIZE_BYTES, remaining)
        sizes.append(s)
        remaining -= s

    try:
        assignment = allocator.assign_blocks(req.block_ids, sizes)
    except allocator.NoDataNodesError as e:
        db.delete_file_by_id(file_id)
        raise HTTPException(status_code=503, detail=str(e))

    blocks = []
    for seq, bid in enumerate(req.block_ids):
        db.add_block(bid, file_id, seq, sizes[seq])
        dn_ids = assignment[bid]
        blocks.append(
            BlockPlan(block_id=bid, seq=seq, datanode_urls=allocator.urls_for(dn_ids))
        )

    return UploadPlanResponse(
        file_id=file_id, block_size=config.BLOCK_SIZE_BYTES, blocks=blocks
    )


@app.post("/files/commit", response_model=CommitResponse)
def commit(req: CommitRequest, user: str = Depends(auth.current_user)):
    db.commit_file(req.file_id)
    return CommitResponse(committed=True)


# ----------------- Descarga / map -----------------
@app.get("/files/map", response_model=FileMapResponse)
def file_map(path: str, user: str = Depends(auth.current_user)):
    path = normalize_path(path)
    f = db.get_file(user, path)
    if not f:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    blocks = []
    for b in db.get_blocks(f["id"]):
        dn_ids = db.get_block_datanodes(b["block_id"])
        urls = allocator.urls_for(dn_ids)
        blocks.append(
            BlockLocation(block_id=b["block_id"], seq=b["seq"], datanode_urls=urls)
        )
    return FileMapResponse(
        file_id=f["id"], path=f["path"], size=f["size"],
        block_size=f["block_size"], blocks=blocks,
    )


@app.delete("/files", response_model=GenericResult)
async def delete_file(path: str, user: str = Depends(auth.current_user)):
    path = normalize_path(path)
    f = db.get_file(user, path)
    if not f:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    # Borrar bloques físicos en los DataNodes.
    blocks = db.get_blocks(f["id"])
    async with httpx.AsyncClient(timeout=30.0) as cli:
        for b in blocks:
            for dn_id in db.get_block_datanodes(b["block_id"]):
                url = db.url_for_datanode(dn_id)
                if not url:
                    continue
                try:
                    await cli.delete(f"{url}/blocks/{b['block_id']}")
                except Exception as e:  # noqa
                    log.warning("No se pudo borrar bloque %s en %s: %s",
                                b["block_id"], url, e)
    db.delete_file_by_id(f["id"])
    return GenericResult(ok=True, detail="Archivo eliminado")


# ----------------- Namespace -----------------
@app.get("/fs/ls", response_model=LsResponse)
def ls(path: str = "/", user: str = Depends(auth.current_user)):
    path = normalize_path(path)
    if not db.dir_exists(user, path):
        raise HTTPException(status_code=404, detail="Directorio no encontrado")
    sub, files = db.list_dir(user, path)
    entries = [LsEntry(name=n, type="dir", size=0) for n in sorted(sub)]
    entries += [LsEntry(name=n, type="file", size=s) for n, s in sorted(files)]
    return LsResponse(entries=entries)


@app.post("/fs/mkdir", response_model=GenericResult)
def mkdir(req: MkdirRequest, user: str = Depends(auth.current_user)):
    path = normalize_path(req.path)
    if path == "/":
        raise HTTPException(status_code=400, detail="La raíz ya existe")
    if db.dir_exists(user, path):
        raise HTTPException(status_code=409, detail="El directorio ya existe")
    if not db.dir_exists(user, parent_dir(path)):
        raise HTTPException(status_code=400, detail="Directorio padre no existe")
    db.mkdir(user, path)
    return GenericResult(ok=True, detail="Directorio creado")


@app.delete("/fs/rmdir", response_model=GenericResult)
def rmdir(path: str, user: str = Depends(auth.current_user)):
    path = normalize_path(path)
    if path == "/":
        raise HTTPException(status_code=400, detail="No se puede borrar la raíz")
    if not db.dir_exists(user, path):
        raise HTTPException(status_code=404, detail="Directorio no encontrado")
    if not db.dir_is_empty(user, path):
        raise HTTPException(status_code=400, detail="El directorio no está vacío")
    db.rmdir(user, path)
    return GenericResult(ok=True, detail="Directorio eliminado")


# ----------------- Heartbeat -----------------
@app.post("/internal/heartbeat", response_model=GenericResult)
def heartbeat(hb: HeartbeatPayload):
    now = time.time()
    db.upsert_datanode(hb.datanode_id, hb.url, hb.free_space_bytes, now)
    db.reconcile_block_locations(hb.datanode_id, hb.block_ids)
    return GenericResult(ok=True)


@app.get("/internal/datanodes")
def list_datanodes():
    now = time.time()
    live = {dn["id"] for dn in db.live_datanodes(now)}
    out = []
    for dn in db.all_datanodes():
        out.append({
            "id": dn["id"], "url": dn["url"],
            "free_space_bytes": dn["free_space_bytes"],
            "alive": dn["id"] in live,
            "last_heartbeat": dn["last_heartbeat"],
        })
    return {"datanodes": out}


@app.get("/health")
def health():
    return {"status": "ok"}


# ----------------- Inspección y mantenimiento -----------------
@app.get("/internal/blocks")
def list_all_blocks():
    """Devuelve todos los bloques con su archivo, secuencia y DataNodes.

    Útil para inspeccionar dónde vive cada bloque y a qué archivo pertenece.
    """
    now = time.time()
    live = {dn["id"] for dn in db.live_datanodes(now)}
    with db._lock:
        rows = db.conn().execute(
            "SELECT b.block_id, b.seq, b.size, f.id AS file_id,"
            "       f.owner, f.path, f.committed"
            " FROM blocks b JOIN files f ON f.id = b.file_id"
            " ORDER BY f.owner, f.path, b.seq"
        ).fetchall()

    per_dn: dict = {}
    blocks_out = []
    for r in rows:
        locs = db.get_block_datanodes(r["block_id"])
        for dn_id in locs:
            per_dn.setdefault(dn_id, 0)
            per_dn[dn_id] += 1
        blocks_out.append({
            "block_id": r["block_id"],
            "file_id": r["file_id"],
            "owner": r["owner"],
            "path": r["path"],
            "seq": r["seq"],
            "size": r["size"],
            "committed": bool(r["committed"]),
            "datanodes": locs,
            "live_replicas": sum(1 for d in locs if d in live),
        })
    return {
        "total_blocks": len(blocks_out),
        "total_replicas": sum(len(b["datanodes"]) for b in blocks_out),
        "per_datanode_count": per_dn,
        "blocks": blocks_out,
    }


@app.post("/internal/gc")
async def gc_orphans():
    """Borra de cada DataNode vivo los bloques físicos que el NameNode no
    referencia (bloques huérfanos: típicamente quedan tras un rm si la copia
    estaba en un DataNode caído o si una sobre-réplica se materializó antes
    de que el catálogo la registrara).
    """
    now = time.time()
    live = db.live_datanodes(now)
    with db._lock:
        known_rows = db.conn().execute("SELECT block_id FROM blocks").fetchall()
    known = {r["block_id"] for r in known_rows}

    deleted_per_dn: dict = {}
    errors = []
    async with httpx.AsyncClient(timeout=30.0) as cli:
        for dn in live:
            try:
                r = await cli.get(f"{dn['url']}/blocks")
                physical = set(r.json().get("block_ids", []))
            except Exception as e:  # noqa
                errors.append({"datanode": dn["id"], "error": str(e)})
                continue
            orphans = physical - known
            deleted = 0
            for bid in orphans:
                try:
                    dr = await cli.delete(f"{dn['url']}/blocks/{bid}")
                    if dr.status_code == 200:
                        deleted += 1
                        # Por si quedó algún registro colgado.
                        db.remove_block_location(bid, dn["id"])
                except Exception as e:  # noqa
                    errors.append({
                        "datanode": dn["id"], "block_id": bid, "error": str(e)
                    })
            deleted_per_dn[dn["id"]] = deleted
    return {
        "ok": True,
        "deleted_per_datanode": deleted_per_dn,
        "errors": errors,
    }
