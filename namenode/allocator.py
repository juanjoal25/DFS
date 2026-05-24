"""Política de distribución: round-robin con peso de espacio libre.

Para cada bloque elige `REPLICATION_FACTOR` DataNodes distintos, prefiriendo
los de mayor espacio libre y decrementando el estimado en memoria por
iteración para balancear progresivamente la carga.
"""
import time
from typing import List, Dict

from common import config
from namenode import db


class NoDataNodesError(Exception):
    pass


def assign_blocks(block_ids: List[str], block_sizes: List[int]) -> Dict[str, List[str]]:
    """Devuelve {block_id: [datanode_id_primario, datanode_id_replica, ...]}."""
    now = time.time()
    live = db.live_datanodes(now)
    rf = config.REPLICATION_FACTOR
    if len(live) < rf:
        raise NoDataNodesError(
            f"Se requieren {rf} DataNodes vivos, hay {len(live)}"
        )

    # Estimado mutable de espacio libre en memoria.
    free = {dn["id"]: dn["free_space_bytes"] for dn in live}

    assignment: Dict[str, List[str]] = {}
    for bid, bsize in zip(block_ids, block_sizes):
        # Ordenar por espacio libre estimado descendente.
        ordered = sorted(free.keys(), key=lambda d: free[d], reverse=True)
        chosen = ordered[:rf]
        assignment[bid] = chosen
        for d in chosen:
            free[d] = max(0, free[d] - bsize)
    return assignment


def urls_for(datanode_ids: List[str]) -> List[str]:
    urls = []
    for d in datanode_ids:
        u = db.url_for_datanode(d)
        if u:
            urls.append(u)
    return urls
