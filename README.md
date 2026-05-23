# DFS por Bloques (estilo HDFS)

Sistema de Archivos Distribuido minimalista orientado a bloques, inspirado en
GFS/HDFS, con semántica **WORM** (Write-Once-Read-Many). Particiona archivos en
bloques configurables, los replica en ≥2 DataNodes, detecta caídas vía heartbeat
y re-replica bloques sub-replicados automáticamente.

Proyecto de *Arquitectura de Nube y Sistemas Distribuidos* — UPB.

## Arquitectura

Arquitectura **Maestro–Trabajadores (Master–Workers)**:

```
                ┌──────────────┐
   CLI cliente ─┤   NameNode   ├─ metadatos (SQLite): namespace,
                │  (FastAPI)   │  archivos→bloques→ubicaciones, usuarios
                └──────┬───────┘
                       │ heartbeats cada 5s / re-replicación
       ┌───────────────┼────────────────┐
   ┌───┴───┐       ┌────┴───┐        ┌───┴────┐
   │DataN.1│       │DataN.2 │        │DataN.3 │   almacenan bloques en disco
   └───────┘       └────────┘        └────────┘
       ▲  transferencia directa de bloques (PUT/GET) + pipeline de réplica
       └──────────── cliente ──────────────┘
```

- **NameNode** (`namenode/`): único punto de control. Gestiona namespace,
  metadatos en SQLite, autenticación (JWT + bcrypt), asignación de bloques
  (round-robin con peso de espacio libre) y re-replicación ante fallos.
- **DataNodes** (`datanode/`): almacenan/sirven bloques, envían heartbeats y
  ejecutan el **pipeline de replicación** (el primario reenvía a la réplica).
- **Cliente CLI** (`client/cli.py`): particiona archivos, negocia con el
  NameNode y transfiere bloques directamente a los DataNodes.

## Componentes y API REST

### Cliente ↔ NameNode
| Método | Endpoint | Descripción |
| --- | --- | --- |
| POST | `/auth/login` | Autenticación, devuelve `{token, expires_in}` |
| POST | `/files/upload-plan` | Plan de escritura: asigna DataNodes a cada bloque |
| POST | `/files/commit` | Confirma archivo tras subir los bloques |
| GET | `/files/map?path=` | Mapa de bloques para descarga |
| DELETE | `/files?path=` | Elimina archivo y sus bloques |
| GET | `/fs/ls?path=` | Lista un directorio |
| POST | `/fs/mkdir` | Crea directorio |
| DELETE | `/fs/rmdir?path=` | Elimina directorio vacío |

### Cliente/DataNode ↔ DataNode
| Método | Endpoint | Descripción |
| --- | --- | --- |
| PUT | `/blocks/{id}` | Guarda bloque. Header `X-Replicas` activa pipeline |
| GET | `/blocks/{id}` | Descarga bloque (stream binario) |
| DELETE | `/blocks/{id}` | Elimina bloque local |
| POST | `/blocks/{id}/replicate` | Copia el bloque a `target_url` (re-replicación) |
| GET | `/health` | Espacio libre, nº de bloques |

### DataNode → NameNode
| Método | Endpoint | Descripción |
| --- | --- | --- |
| POST | `/internal/heartbeat` | `{datanode_id, url, free_space_bytes, block_ids}` cada 5s |
| GET | `/internal/datanodes` | Estado de todos los DataNodes (vivo/muerto) |

## Algoritmos

- **Particionamiento** (cliente): `N = ceil(file_size / block_size)`, un
  `uuid4()` por bloque; lee chunks de `block_size` y los envía.
- **Distribución** (NameNode, `allocator.py`): ordena DataNodes vivos por
  espacio libre desc., asigna primario + réplica distintos por bloque,
  decrementa el estimado en memoria por iteración (balanceo progresivo).
- **Replicación en pipeline** (DataNode): el primario guarda y reenvía al
  siguiente de `X-Replicas`; confirma al cliente cuando la cadena confirma.
- **Re-replicación** (NameNode, `replication.py`): tarea de fondo que marca
  DataNodes sin heartbeat en `HEARTBEAT_TIMEOUT`s como muertos, detecta bloques
  con <`REPLICATION_FACTOR` réplicas vivas y ordena copiarlos a un nodo vivo.

## Variables de entorno

| Variable | Default | Descripción |
| --- | --- | --- |
| `BLOCK_SIZE_MB` | 64 | Tamaño de bloque |
| `REPLICATION_FACTOR` | 2 | Réplicas por bloque |
| `HEARTBEAT_INTERVAL` | 5 | Segundos entre heartbeats |
| `HEARTBEAT_TIMEOUT` | 15 | Segundos para marcar un DataNode muerto |
| `NAMENODE_URL` | `http://localhost:8000` | URL del NameNode |
| `DATA_DIR` | `./data/blocks` | Carpeta de bloques (DataNode) |
| `DATANODE_PUBLIC_URL` | `http://localhost:8100` | URL pública del DataNode |
| `DB_PATH` | `namenode.db` | Ruta de la BD SQLite |
| `SEED_USERS` | `admin:admin` | Usuarios semilla `user:pass,...` |

## Ejecución local con Docker

```bash
# Levantar 1 NameNode + 3 DataNodes
BLOCK_SIZE_MB=64 docker compose up --build -d

# Usar la CLI dentro de la red (las URLs de DataNode son service names)
docker compose run --rm client login admin admin
docker compose run --rm client mkdir /docs
docker compose run --rm client ls /

# Generar archivo de prueba y subirlo (se monta ./testdata en /testdata)
python scripts/gen_testfile.py testdata/orig.bin 512MB
docker compose run --rm client put /testdata/orig.bin /docs/archivo.bin
docker compose run --rm client get /docs/archivo.bin /testdata/out.bin

# Tolerancia a fallos
docker compose stop datanode2          # esperar > HEARTBEAT_TIMEOUT
docker compose run --rm client get /docs/archivo.bin /testdata/out2.bin
```

Inspección de distribución de bloques:
```bash
docker compose exec datanode1 ls -1 /data/blocks | wc -l
curl http://localhost:8000/internal/datanodes
```

## Ejecución local nativa (sin Docker)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\

# Terminal 1 — NameNode
BLOCK_SIZE_MB=1 uvicorn namenode.main:app --port 8000

# Terminales 2-4 — DataNodes
DATANODE_ID=dn1 DATANODE_PUBLIC_URL=http://127.0.0.1:8101 DATA_DIR=./d1 uvicorn datanode.main:app --port 8101
DATANODE_ID=dn2 DATANODE_PUBLIC_URL=http://127.0.0.1:8102 DATA_DIR=./d2 uvicorn datanode.main:app --port 8102
DATANODE_ID=dn3 DATANODE_PUBLIC_URL=http://127.0.0.1:8103 DATA_DIR=./d3 uvicorn datanode.main:app --port 8103

# Terminal 5 — Cliente
BLOCK_SIZE_MB=1 python -m client.cli login admin admin
python -m client.cli mkdir /docs
python -m client.cli put orig.bin /docs/archivo.bin
python -m client.cli get /docs/archivo.bin out.bin
```

> En Windows usa PowerShell para los comandos de la CLI: Git Bash convierte las
> rutas que empiezan con `/` (p. ej. `/docs`) a rutas de Windows.

## Despliegue en AWS Academy (Terraform)

`infra/` aprovisiona 4× EC2 t2.micro (1 NameNode + 3 DataNodes) con Docker,
clonando el repo y arrancando el contenedor correspondiente vía user-data.

```bash
cd infra
terraform init
terraform apply \
  -var="key_name=<tu-keypair>" \
  -var="git_repo=https://github.com/<usuario>/<repo>.git"
# Salidas: namenode_url y datanode_urls (IPs públicas)
```

Luego, desde tu máquina:
```bash
NAMENODE_URL=http://<ip-namenode>:8000 python -m client.cli login admin admin
```

> Verifica/actualiza `ami_id` (Ubuntu 22.04 de tu región) y usa el key pair
> existente en tu cuenta de AWS Academy. Las credenciales se configuran con
> `aws configure` usando las claves temporales del laboratorio.

## Pruebas

```bash
pip install pytest
pytest tests/
```

Las pruebas unitarias cubren normalización de rutas y particionamiento. La
verificación end-to-end (put/get + re-replicación tras matar un DataNode) se
realiza con el flujo de la CLI descrito arriba, comparando el SHA-256 del
archivo original con el descargado.

## Modelo de seguridad / alcance

- Autenticación básica por usuario (JWT con expiración, contraseñas con bcrypt).
- Cada usuario solo ve y gestiona sus propios archivos.
- Semántica WORM: un `put` reemplaza el archivo completo (sin actualización
  parcial de bloques).
- Acceso a SQLite serializado con lock (suficiente para el alcance).
