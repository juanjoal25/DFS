"""DataNode: almacena/sirve bloques, hace pipeline de réplica y heartbeats."""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from common import config
from datanode import storage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("datanode")


async def heartbeat_loop():
    url = f"{config.NAMENODE_URL}/internal/heartbeat"
    while True:
        try:
            payload = {
                "datanode_id": config.DATANODE_ID,
                "url": config.DATANODE_PUBLIC_URL,
                "free_space_bytes": storage.free_space_bytes(),
                "block_ids": storage.list_blocks(),
            }
            async with httpx.AsyncClient(timeout=10.0) as cli:
                await cli.post(url, json=payload)
        except Exception as e:  # noqa
            log.warning("Heartbeat fallido: %s", e)
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init()
    task = asyncio.create_task(heartbeat_loop())
    log.info("DataNode %s iniciado. DATA_DIR=%s", config.DATANODE_ID, config.DATA_DIR)
    yield
    task.cancel()


app = FastAPI(title=f"DFS DataNode {config.DATANODE_ID}", lifespan=lifespan)


class ReplicateRequest(BaseModel):
    target_url: str


@app.put("/blocks/{block_id}")
async def put_block(block_id: str, request: Request):
    data = await request.body()
    storage.write_block(block_id, data)

    # Pipeline de replicación: reenviar a la siguiente réplica de la lista.
    replicas_header = request.headers.get("X-Replicas", "")
    replicas = [r.strip() for r in replicas_header.split(",") if r.strip()]
    # Quitar mi propia URL de la lista.
    replicas = [r for r in replicas if r.rstrip("/") != config.DATANODE_PUBLIC_URL.rstrip("/")]

    if replicas:
        next_url = replicas[0]
        remaining = replicas[1:]
        headers = {"X-Replicas": ",".join(remaining)} if remaining else {}
        try:
            async with httpx.AsyncClient(timeout=300.0) as cli:
                r = await cli.put(
                    f"{next_url}/blocks/{block_id}", content=data, headers=headers
                )
                if r.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Réplica {next_url} respondió {r.status_code}",
                    )
        except HTTPException:
            raise
        except Exception as e:  # noqa
            raise HTTPException(status_code=502, detail=f"Pipeline falló: {e}")

    return {"stored": True, "block_id": block_id, "size": len(data)}


@app.get("/blocks/{block_id}")
def get_block(block_id: str):
    if not storage.exists(block_id):
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    return Response(content=storage.read_block(block_id),
                    media_type="application/octet-stream")


@app.delete("/blocks/{block_id}")
def delete_block(block_id: str):
    deleted = storage.delete_block(block_id)
    return {"deleted": deleted}


@app.post("/blocks/{block_id}/replicate")
async def replicate_block(block_id: str, req: ReplicateRequest):
    """Ordenado por el NameNode: copiar este bloque a target_url."""
    if not storage.exists(block_id):
        raise HTTPException(status_code=404, detail="Bloque no encontrado")
    data = storage.read_block(block_id)
    try:
        async with httpx.AsyncClient(timeout=300.0) as cli:
            r = await cli.put(f"{req.target_url}/blocks/{block_id}", content=data)
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail="Destino rechazó el bloque")
    except HTTPException:
        raise
    except Exception as e:  # noqa
        raise HTTPException(status_code=502, detail=f"Copia falló: {e}")
    return {"replicated": True, "target": req.target_url}


@app.get("/health")
def health():
    return {
        "datanode_id": config.DATANODE_ID,
        "free_space_bytes": storage.free_space_bytes(),
        "block_count": len(storage.list_blocks()),
    }


@app.get("/blocks")
def list_local_blocks():
    """Lista los block_ids almacenados físicamente en este DataNode."""
    return {"datanode_id": config.DATANODE_ID, "block_ids": storage.list_blocks()}
