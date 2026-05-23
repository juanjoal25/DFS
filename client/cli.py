"""Cliente CLI del DFS.

Uso:
    python -m client.cli login <usuario> <password>
    python -m client.cli put <archivo_local> <ruta_dfs>
    python -m client.cli get <ruta_dfs> <destino_local>
    python -m client.cli ls [ruta]
    python -m client.cli rm <ruta_dfs>
    python -m client.cli mkdir <ruta_dfs>
    python -m client.cli rmdir <ruta_dfs>
"""
import math
import os
import sys
import uuid
import json
import hashlib
from pathlib import Path

import httpx
import typer

app = typer.Typer(add_completion=False, help="Cliente DFS por bloques")

NAMENODE_URL = os.environ.get("NAMENODE_URL", "http://localhost:8000")
CONFIG_DIR = Path(os.environ.get("DFS_HOME", str(Path.home() / ".dfs")))
TOKEN_FILE = CONFIG_DIR / "token"
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def _save_token(token: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)


def _load_token() -> str:
    if not TOKEN_FILE.exists():
        typer.secho("No autenticado. Ejecuta 'login' primero.", fg="red")
        raise typer.Exit(1)
    return TOKEN_FILE.read_text().strip()


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_load_token()}"}


def _check(resp: httpx.Response):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa
            detail = resp.text
        typer.secho(f"Error {resp.status_code}: {detail}", fg="red")
        raise typer.Exit(1)


@app.command()
def login(username: str, password: str):
    """Autenticarse y guardar el token."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.post(f"{NAMENODE_URL}/auth/login",
                     json={"username": username, "password": password})
    _check(r)
    _save_token(r.json()["token"])
    typer.secho(f"Autenticado como {username}.", fg="green")


@app.command()
def put(local_file: str, dfs_path: str):
    """Subir un archivo, particionándolo en bloques y replicándolo."""
    src = Path(local_file)
    if not src.is_file():
        typer.secho(f"Archivo no existe: {local_file}", fg="red")
        raise typer.Exit(1)

    size = src.stat().st_size
    # Pedir tamaño de bloque al NameNode vía upload-plan (el NameNode decide).
    # Primero generamos block_ids; pero necesitamos block_size. Usamos un
    # plan en dos pasos: pedimos plan con block_ids calculados localmente
    # usando el block_size que devuelva el NameNode. Para evitar ida y vuelta,
    # consultamos el block_size con un plan de tamaño conocido.
    block_size = _negotiate_block_size()
    n_blocks = max(1, math.ceil(size / block_size))
    block_ids = [str(uuid.uuid4()) for _ in range(n_blocks)]

    headers = _auth_headers()
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.post(f"{NAMENODE_URL}/files/upload-plan", headers=headers,
                     json={"path": dfs_path, "size": size, "block_ids": block_ids})
        _check(r)
        plan = r.json()
        block_size = plan["block_size"]
        blocks = {b["seq"]: b for b in plan["blocks"]}

        with src.open("rb") as f:
            for seq in range(len(block_ids)):
                chunk = f.read(block_size)
                b = blocks[seq]
                urls = b["datanode_urls"]
                if not urls:
                    typer.secho(f"Bloque {seq} sin DataNodes asignados", fg="red")
                    raise typer.Exit(1)
                primary = urls[0]
                replicas = ",".join(urls)  # primario se auto-excluye
                pr = cli.put(f"{primary}/blocks/{b['block_id']}",
                             content=chunk, headers={"X-Replicas": replicas})
                _check(pr)
                typer.echo(f"  bloque {seq+1}/{len(block_ids)} -> "
                           f"{', '.join(urls)}")

        cr = cli.post(f"{NAMENODE_URL}/files/commit", headers=headers,
                      json={"file_id": plan["file_id"]})
        _check(cr)
    typer.secho(f"Subido {dfs_path} ({n_blocks} bloques, {size} bytes).", fg="green")


def _negotiate_block_size() -> int:
    # El NameNode expone block_size en cada plan; usamos un valor por defecto
    # conservador para particionar y luego respetamos el del plan real.
    return int(os.environ.get("BLOCK_SIZE_MB", "64")) * 1024 * 1024


@app.command()
def get(dfs_path: str, dest: str):
    """Descargar un archivo reconstruyéndolo desde sus bloques."""
    headers = _auth_headers()
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.get(f"{NAMENODE_URL}/files/map", headers=headers,
                    params={"path": dfs_path})
        _check(r)
        fmap = r.json()
        blocks = sorted(fmap["blocks"], key=lambda b: b["seq"])

        with open(dest, "wb") as out:
            for b in blocks:
                data = _fetch_block(cli, b)
                out.write(data)
    sha = _sha256(dest)
    typer.secho(f"Descargado {dfs_path} -> {dest} "
                f"({fmap['size']} bytes, sha256={sha[:16]}…).", fg="green")


def _fetch_block(cli: httpx.Client, b: dict) -> bytes:
    last_err = None
    for url in b["datanode_urls"]:
        try:
            r = cli.get(f"{url}/blocks/{b['block_id']}")
            if r.status_code == 200:
                return r.content
            last_err = f"{url} -> {r.status_code}"
        except Exception as e:  # noqa
            last_err = f"{url} -> {e}"
    typer.secho(f"No se pudo recuperar el bloque {b['block_id']} "
                f"(seq {b['seq']}): {last_err}", fg="red")
    raise typer.Exit(1)


@app.command()
def ls(path: str = typer.Argument("/")):
    """Listar un directorio."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.get(f"{NAMENODE_URL}/fs/ls", headers=_auth_headers(),
                    params={"path": path})
    _check(r)
    for e in r.json()["entries"]:
        flag = "d" if e["type"] == "dir" else "-"
        typer.echo(f"{flag}  {e['size']:>12}  {e['name']}")


@app.command()
def rm(dfs_path: str):
    """Eliminar un archivo y sus bloques."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.delete(f"{NAMENODE_URL}/files", headers=_auth_headers(),
                       params={"path": dfs_path})
    _check(r)
    typer.secho(f"Eliminado {dfs_path}.", fg="green")


@app.command()
def mkdir(dfs_path: str):
    """Crear un directorio."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.post(f"{NAMENODE_URL}/fs/mkdir", headers=_auth_headers(),
                     json={"path": dfs_path})
    _check(r)
    typer.secho(f"Directorio creado: {dfs_path}.", fg="green")


@app.command()
def rmdir(dfs_path: str):
    """Eliminar un directorio vacío."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.delete(f"{NAMENODE_URL}/fs/rmdir", headers=_auth_headers(),
                       params={"path": dfs_path})
    _check(r)
    typer.secho(f"Directorio eliminado: {dfs_path}.", fg="green")


@app.command()
def blocks(path: str = typer.Option(None, "--path", "-p",
                                    help="Filtrar por prefijo de ruta DFS"),
           datanode: str = typer.Option(None, "--dn",
                                        help="Filtrar por id de DataNode")):
    """Listar todos los bloques con su archivo, secuencia y DataNodes."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.get(f"{NAMENODE_URL}/internal/blocks")
    _check(r)
    data = r.json()

    rows = data["blocks"]
    if path:
        rows = [b for b in rows if b["path"].startswith(path)]
    if datanode:
        rows = [b for b in rows if datanode in b["datanodes"]]

    # Encabezado
    typer.secho(
        f"{'archivo':<30} {'seq':>4} {'size':>10}  {'block_id':<36}  datanodes",
        fg="cyan",
    )
    cur_path = None
    for b in rows:
        if b["path"] != cur_path:
            cur_path = b["path"]
        name = b["path"] if len(b["path"]) <= 30 else "…" + b["path"][-29:]
        dns = ",".join(b["datanodes"]) or "(sin réplicas)"
        typer.echo(f"{name:<30} {b['seq']:>4} {b['size']:>10}  "
                   f"{b['block_id']:<36}  {dns}")

    typer.secho(
        f"\nTotal bloques: {data['total_blocks']}  "
        f"Total réplicas: {data['total_replicas']}",
        fg="green",
    )
    if data.get("per_datanode_count"):
        parts = [f"{k}={v}" for k, v in sorted(data["per_datanode_count"].items())]
        typer.secho("Réplicas por DataNode: " + "  ".join(parts), fg="green")


@app.command()
def gc():
    """Limpiar bloques huérfanos en los DataNodes (los que el NameNode ya no
    referencia)."""
    with httpx.Client(timeout=TIMEOUT) as cli:
        r = cli.post(f"{NAMENODE_URL}/internal/gc")
    _check(r)
    data = r.json()
    typer.secho("GC completado.", fg="green")
    for dn, n in data.get("deleted_per_datanode", {}).items():
        typer.echo(f"  {dn}: {n} bloques huérfanos borrados")
    for err in data.get("errors", []):
        typer.secho(f"  error: {err}", fg="yellow")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    app()
