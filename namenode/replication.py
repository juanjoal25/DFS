"""Re-replicación y grooming: mantiene el factor de replicación objetivo.

Tarea de fondo periódica que:
  1. Determina qué DataNodes están vivos (heartbeat reciente).
  2. Busca bloques con menos de REPLICATION_FACTOR réplicas vivas y ordena
     copias adicionales vía POST /blocks/{id}/replicate.
  3. Busca bloques con más réplicas vivas de las necesarias (sobre-replicación,
     típicamente tras el regreso de un DataNode que había sido marcado caído) y
     ordena el borrado de las copias sobrantes vía DELETE /blocks/{id}.
"""
import asyncio
import time
import logging

import httpx

from common import config
from namenode import db

log = logging.getLogger("namenode.replication")


async def _replicate_block(block_id: str, source_url: str, target_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=300.0) as cli:
            r = await cli.post(
                f"{source_url}/blocks/{block_id}/replicate",
                json={"target_url": target_url},
            )
            return r.status_code == 200
    except Exception as e:  # noqa
        log.warning("Fallo replicando %s -> %s: %s", block_id, target_url, e)
        return False


async def _delete_block(block_id: str, datanode_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            r = await cli.delete(f"{datanode_url}/blocks/{block_id}")
            return r.status_code == 200
    except Exception as e:  # noqa
        log.warning("Fallo borrando réplica %s en %s: %s", block_id, datanode_url, e)
        return False


async def run_once():
    now = time.time()
    live = db.live_datanodes(now)
    live_ids = {dn["id"] for dn in live}
    if len(live_ids) < config.REPLICATION_FACTOR:
        return  # No hay suficientes nodos para re-replicar.

    url_by_id = {dn["id"]: dn["url"] for dn in live}

    # Recorrer todos los bloques conocidos.
    with db._lock:
        blocks = db.conn().execute(
            "SELECT b.block_id FROM blocks b"
            " JOIN files f ON f.id=b.file_id WHERE f.committed=1"
        ).fetchall()

    for row in blocks:
        bid = row["block_id"]
        locations = db.get_block_datanodes(bid)
        live_locs = [d for d in locations if d in live_ids]

        if len(live_locs) == 0:
            log.error("Bloque %s sin réplicas vivas: irrecuperable", bid)
            continue
        if len(live_locs) > config.REPLICATION_FACTOR:
            # Sobre-replicado: borrar copias sobrantes. Conservamos las
            # ubicaciones con MÁS espacio libre y descartamos las que tengan
            # menos, para equilibrar el uso del clúster.
            free_by_id = {dn["id"]: dn["free_space_bytes"] for dn in live}
            ordered = sorted(live_locs, key=lambda d: free_by_id.get(d, 0))
            excess = len(live_locs) - config.REPLICATION_FACTOR
            for dn_id in ordered[:excess]:
                ok = await _delete_block(bid, url_by_id[dn_id])
                if ok:
                    db.remove_block_location(bid, dn_id)
                    log.info("Groom: réplica sobrante de %s borrada en %s",
                             bid, dn_id)
            continue
        if len(live_locs) == config.REPLICATION_FACTOR:
            continue

        # Sub-replicado: elegir destinos nuevos.
        candidates = sorted(
            (dn for dn in live if dn["id"] not in live_locs),
            key=lambda d: d["free_space_bytes"],
            reverse=True,
        )
        needed = config.REPLICATION_FACTOR - len(live_locs)
        source_url = url_by_id[live_locs[0]]
        for dest in candidates[:needed]:
            ok = await _replicate_block(bid, source_url, dest["url"])
            if ok:
                db.add_block_location(bid, dest["id"])
                log.info("Re-replicado %s en %s", bid, dest["id"])


async def loop():
    while True:
        try:
            await run_once()
        except Exception as e:  # noqa
            log.exception("Error en ciclo de re-replicación: %s", e)
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)
