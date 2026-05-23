# Guía de ejecución y pruebas del DFS

Esta guía deja **solo** los pasos secuenciales para probar todos los comandos
del cliente, indicando claramente **dónde se ejecuta cada bloque**:

- **Opción A — Docker con CLI persistente**.
- **Opción B — Terraform / AWS** (CLI local apuntando al NameNode remoto).

Requisitos: Python 3.11+, Docker Desktop y (para la opción B) AWS CLI +
Terraform.

---

## 1. Cómo funciona

1. El **cliente** se autentica contra el **NameNode** y recibe un token.
2. `put`: el cliente parte el archivo en bloques de `BLOCK_SIZE_MB`, genera un
   `uuid4` por bloque y pide un **plan** al NameNode (elige 2 DataNodes por
   bloque, el de más espacio libre primero).
3. El cliente envía cada bloque al **DataNode primario** con `X-Replicas`. El
   primario lo guarda y lo **reenvía en pipeline** a la réplica. Al terminar,
   `commit` marca el archivo como disponible.
4. `get`: el cliente pide el **mapa de bloques** y los descarga (si una
   réplica falla, prueba la otra) y reconstruye el archivo.
5. Heartbeat cada 5 s; tras 15 s sin él, el NameNode marca al DN como muerto
   y la tarea de fondo **re-replica** o **groomea** (borra sobre-réplicas) para
   mantener el factor de replicación.
6. **GC** borra bloques huérfanos (físicos en disco que el NameNode ya no
   referencia).

| Comando | Descripción |
| --- | --- |
| `login <user> <pass>` | Autenticarse y guardar token |
| `mkdir <ruta>` | Crear directorio |
| `ls [ruta]` | Listar directorio (default `/`) |
| `put <local> <ruta_dfs>` | Subir archivo (particiona + replica) |
| `get <ruta_dfs> <local>` | Descargar y reconstruir |
| `rm <ruta_dfs>` | Borrar archivo y sus bloques |
| `rmdir <ruta>` | Borrar directorio vacío |
| `blocks [--path P] [--dn ID]` | Listar bloques con archivo, seq y datanodes |
| `gc` | Borrar bloques huérfanos en los DataNodes |

---

## 2. Opción A — Docker con CLI persistente

> **Dónde se ejecutan estos comandos:** en una terminal del **host**
> (PowerShell o bash), en la raíz del repo. La CLI corre dentro del
> contenedor `dfs` mediante `docker compose exec dfs dfs …`.

### 2.1 Levantar el clúster + cliente persistente

```bash
# Host
mkdir -p testdata
BLOCK_SIZE_MB=64 docker compose up --build -d \
  namenode datanode1 datanode2 datanode3 dfs

# Verificar
curl http://localhost:8000/internal/datanodes
```

El servicio `dfs` queda arriba con un `tail -f /dev/null`: no hay que recrear
contenedor en cada comando, basta `docker compose exec dfs dfs …`. El token de
sesión se persiste en el volumen `client_home`.



### 2.2 Login

```bash
# Host
docker compose exec dfs bash
# CLI
dfs login admin admin
```

### 2.3 Crear árbol de directorios

```bash
# CLI
dfs mkdir /docs
dfs mkdir /docs/2025
dfs mkdir /docs/2025/informes
dfs mkdir /media
dfs mkdir /media/video
dfs mkdir /tmp

dfs ls /
dfs ls /docs
dfs ls /docs/2025
dfs ls /docs/2025/informes
```

### 2.4 Generar archivos de prueba (host)

`./testdata` del host se monta en `/testdata` del contenedor.

```bash
# Host
python scripts/gen_testfile.py testdata/small.bin   2MB
python scripts/gen_testfile.py testdata/medium.bin 80MB
python scripts/gen_testfile.py testdata/big.bin   250MB
python scripts/gen_testfile.py testdata/huge.bin  700MB
```

### 2.5 Subir archivos a distintos directorios

```bash
# CLI
dfs put /testdata/small.bin  /tmp/small.bin
dfs put /testdata/medium.bin /docs/2025/medium.bin
dfs put /testdata/big.bin    /docs/2025/informes/big.bin
dfs put /testdata/huge.bin   /media/video/huge.bin

dfs ls /tmp
dfs ls /docs/2025
dfs ls /docs/2025/informes
dfs ls /media/video
```

### 2.6 Inspeccionar la distribución de bloques

```bash
# CLI
dfs blocks                    # vista global
dfs blocks --path /docs/2025  # por subárbol
dfs blocks --dn datanode2     # por DataNode

# HOST
# Conteo crudo por DataNode
docker compose exec datanode1 sh -c "ls -1 /data/blocks | wc -l"
docker compose exec datanode2 sh -c "ls -1 /data/blocks | wc -l"
docker compose exec datanode3 sh -c "ls -1 /data/blocks | wc -l"
```

### 2.7 Descargar y verificar integridad

```bash
# CLI
dfs get /tmp/small.bin              /testdata/out_small.bin
dfs get /docs/2025/medium.bin       /testdata/out_medium.bin
dfs get /docs/2025/informes/big.bin /testdata/out_big.bin
dfs get /media/video/huge.bin       /testdata/out_huge.bin
```

# Verificación de SHA-256 (host, bash)

```bash
# Host
for pair in "small.bin:out_small.bin" "medium.bin:out_medium.bin" \
            "big.bin:out_big.bin" "huge.bin:out_huge.bin"; do
  a=${pair%%:*}; b=${pair##*:}
  ha=$(sha256sum testdata/$a | awk '{print $1}')
  hb=$(sha256sum testdata/$b | awk '{print $1}')
  [ "$ha" = "$hb" ] && echo "$a -> $b : OK" || echo "$a -> $b : MISMATCH"
done
```

En PowerShell:
```powershell
# Host
foreach ($pair in @(
    @('testdata/small.bin','testdata/out_small.bin'),
    @('testdata/medium.bin','testdata/out_medium.bin'),
    @('testdata/big.bin','testdata/out_big.bin'),
    @('testdata/huge.bin','testdata/out_huge.bin'))) {
  $a = (Get-FileHash $pair[0] -Algorithm SHA256).Hash
  $b = (Get-FileHash $pair[1] -Algorithm SHA256).Hash
  "$($pair[0]) -> $($pair[1]) : " + ($(if ($a -eq $b) {'OK'} else {'MISMATCH'}))
}
```

### 2.8 Sobrescribir un archivo (WORM por archivo: reemplaza el anterior)

```bash
# Host
python scripts/gen_testfile.py testdata/small.v2.bin 3MB

# CLI
dfs put /testdata/small.v2.bin /tmp/small.bin
dfs get /tmp/small.bin /testdata/out_small_v2.bin

sha256sum testdata/small.v2.bin testdata/out_small_v2.bin
```


### 2.9 Tolerancia a fallos (matar un DataNode)

```bash
# Host
docker compose stop datanode2
sleep 20  # > HEARTBEAT_TIMEOUT (15s) + ciclo de re-replicación

curl -s http://localhost:8000/internal/datanodes | jq '.datanodes[] | {id,alive}'

dfs get /docs/2025/medium.bin /testdata/out_medium_dn2off.bin

sha256sum testdata/medium.bin testdata/out_medium_dn2off.bin   # deben coincidir

# Reencender y observar over/under-replication + groom
docker compose start datanode2
#O si fue eliminado: docker compose up -d datanode2
sleep 30
docker compose exec dfs dfs blocks
```

### 2.10 Borrar archivos

```bash
# Host
dfs rm /tmp/small.bin
dfs ls /tmp

dfs rm /docs/2025/medium.bin
dfs rm /docs/2025/informes/big.bin

dfs ls /docs/2025
dfs ls /docs/2025/informes

dfs rm /media/video/huge.bin
dfs ls /media/video
```

### 2.11 Borrar directorios (de hoja a raíz)

```bash
# Host
dfs rmdir /docs/2025/informes
dfs rmdir /docs/2025
dfs rmdir /docs
dfs rmdir /media/video
dfs rmdir /media
dfs rmdir /tmp

dfs dfs ls /
```

### 2.12 Limpiar bloques huérfanos (gc)

Si quedan ficheros físicos sin referencia (p. ej. tras un `rm` con un DN
caído, o sobre-réplicas materializadas tarde):

```bash
# Host
docker compose exec dfs dfs gc

docker compose exec datanode1 sh -c "ls -1 /data/blocks | wc -l"
docker compose exec datanode2 sh -c "ls -1 /data/blocks | wc -l"
docker compose exec datanode3 sh -c "ls -1 /data/blocks | wc -l"
```

### 2.13 Apagar el clúster

```bash
# Host
docker compose down -v
```

---

## 3. Opción B — Terraform / AWS Academy

> **Dónde se ejecutan estos comandos:** los pasos 3.1 son en el **host local**
> para desplegar; los pasos 3.2 en adelante son en el **host local** ejecutando
> la CLI nativa apuntando a la IP pública del NameNode en EC2.

### 3.1 Desplegar

```bash
# Host
aws configure   # access key, secret, session token de AWS Academy

cd infra
terraform init
terraform apply \
  -var="key_name=<tu-keypair>" \
  -var="git_repo=https://github.com/<usuario>/<repo>.git"
terraform output
# Anota: namenode_url, datanode_urls
cd ..
```

Revisa antes `infra/variables.tf` (`ami_id`, `aws_region`, `block_size_mb`).
Cada EC2 instala Docker, clona el repo y arranca su contenedor vía user-data.
Abre en el Security Group los puertos `8000` y `8101-8103` desde tu IP.

### 3.2 Preparar la CLI local

```powershell
# Host (PowerShell)
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

$env:NAMENODE_URL = "http://<ip-publica-namenode>:8000"
$env:BLOCK_SIZE_MB = "64"
$env:DFS_HOME = ".aws/dfshome"
$PY = ".venv\Scripts\python.exe"
mkdir .aws -ea 0
```

En bash sustituye `$env:VAR =` por `export VAR=` y `$PY` por `python`.

### 3.3 Login

```powershell
# Host
& $PY -m client.cli login admin admin
```

### 3.4 Crear árbol de directorios

```powershell
# Host
& $PY -m client.cli mkdir /docs
& $PY -m client.cli mkdir /docs/2025
& $PY -m client.cli mkdir /docs/2025/informes
& $PY -m client.cli mkdir /media
& $PY -m client.cli mkdir /media/video
& $PY -m client.cli mkdir /tmp
& $PY -m client.cli ls /
```

### 3.5 Generar archivos de prueba

```powershell
# Host (ojo al ancho de banda hacia AWS)
& $PY scripts/gen_testfile.py .aws/small.bin   2MB
& $PY scripts/gen_testfile.py .aws/medium.bin 80MB
& $PY scripts/gen_testfile.py .aws/big.bin   250MB
# Opcional, puede tardar mucho por internet:
# & $PY scripts/gen_testfile.py .aws/huge.bin  700MB
```

### 3.6 Subir archivos

```powershell
# Host
& $PY -m client.cli put .aws/small.bin  /tmp/small.bin
& $PY -m client.cli put .aws/medium.bin /docs/2025/medium.bin
& $PY -m client.cli put .aws/big.bin    /docs/2025/informes/big.bin

& $PY -m client.cli ls /tmp
& $PY -m client.cli ls /docs/2025
& $PY -m client.cli ls /docs/2025/informes
```

### 3.7 Inspeccionar la distribución de bloques

```powershell
# Host
& $PY -m client.cli blocks
& $PY -m client.cli blocks --path /docs/2025
& $PY -m client.cli blocks --dn datanode2

# Health crudo por DataNode (sustituye las IPs por las de terraform output)
foreach ($u in @("<ip-dn1>:8101","<ip-dn2>:8102","<ip-dn3>:8103")) {
  & $PY -c "import httpx,sys; print(sys.argv[1], httpx.get('http://'+sys.argv[1]+'/health').json())" $u
}
```

### 3.8 Descargar y verificar integridad

```powershell
# Host
& $PY -m client.cli get /tmp/small.bin              .aws/out_small.bin
& $PY -m client.cli get /docs/2025/medium.bin       .aws/out_medium.bin
& $PY -m client.cli get /docs/2025/informes/big.bin .aws/out_big.bin

foreach ($pair in @(
    @('.aws/small.bin','.aws/out_small.bin'),
    @('.aws/medium.bin','.aws/out_medium.bin'),
    @('.aws/big.bin','.aws/out_big.bin'))) {
  $a = (Get-FileHash $pair[0] -Algorithm SHA256).Hash
  $b = (Get-FileHash $pair[1] -Algorithm SHA256).Hash
  "$($pair[0]) -> $($pair[1]) : " + ($(if ($a -eq $b) {'OK'} else {'MISMATCH'}))
}
```

### 3.9 Sobrescribir un archivo

```powershell
# Host
& $PY scripts/gen_testfile.py .aws/small.v2.bin 3MB
& $PY -m client.cli put .aws/small.v2.bin /tmp/small.bin
& $PY -m client.cli get /tmp/small.bin .aws/out_small_v2.bin
(Get-FileHash .aws/small.v2.bin     -Algorithm SHA256).Hash
(Get-FileHash .aws/out_small_v2.bin -Algorithm SHA256).Hash
```

### 3.10 Tolerancia a fallos

```powershell
# Conectarse a la EC2 del DN2 y pararlo:
# ssh -i <key.pem> ubuntu@<ip-dn2> "docker stop datanode2"

# Tras > 20 s:
& $PY -m client.cli get /docs/2025/medium.bin .aws/out_medium_dn2off.bin
(Get-FileHash .aws/medium.bin            -Algorithm SHA256).Hash
(Get-FileHash .aws/out_medium_dn2off.bin -Algorithm SHA256).Hash

# Reencender y observar groom/re-replicación
# ssh -i <key.pem> ubuntu@<ip-dn2> "docker start datanode2"
Start-Sleep 30
& $PY -m client.cli blocks
```

### 3.11 Borrar archivos

```powershell
# Host
& $PY -m client.cli rm /tmp/small.bin
& $PY -m client.cli rm /docs/2025/medium.bin
& $PY -m client.cli rm /docs/2025/informes/big.bin
& $PY -m client.cli ls /docs/2025
& $PY -m client.cli ls /docs/2025/informes
```

### 3.12 Borrar directorios (de hoja a raíz)

```powershell
# Host
& $PY -m client.cli rmdir /docs/2025/informes
& $PY -m client.cli rmdir /docs/2025
& $PY -m client.cli rmdir /docs
& $PY -m client.cli rmdir /media/video
& $PY -m client.cli rmdir /media
& $PY -m client.cli rmdir /tmp
& $PY -m client.cli ls /
```

### 3.13 Limpiar bloques huérfanos (gc)

```powershell
# Host
& $PY -m client.cli gc

# Verificar via /health (block_count debería bajar)
foreach ($u in @("<ip-dn1>:8101","<ip-dn2>:8102","<ip-dn3>:8103")) {
  & $PY -c "import httpx,sys; print(sys.argv[1], httpx.get('http://'+sys.argv[1]+'/health').json())" $u
}
```

### 3.14 Destruir todo

```bash
# Host
cd infra
terraform destroy
```

---

## 4. Acceso directo a la base SQLite

Todo el **namespace** (usuarios, directorios, archivos, bloques y en qué
DataNode vive cada bloque) está en **SQLite** en el NameNode. La jerarquía
de carpetas existe **solo como filas** en estas tablas
(ver [namenode/db.py](namenode/db.py)):

- `directories(owner, path)` — directorios por usuario.
- `files(id, owner, path, size, block_size, committed)` — metadatos de archivo.
- `blocks(block_id, file_id, seq, size)` — bloques de cada archivo.
- `block_locations(block_id, datanode_id)` — en qué DataNode vive cada réplica.
- `datanodes(id, url, free_space_bytes, last_heartbeat)` — estado del clúster.

### 4.1 Docker (BD en volumen `namenode_data`, montado en `/data`)

```bash
# Host
docker compose exec namenode sh -c "apt-get update >/dev/null && apt-get install -y sqlite3 >/dev/null"

docker compose exec namenode sqlite3 /data/namenode.db ".tables"
docker compose exec namenode sqlite3 /data/namenode.db \
  "SELECT owner, path FROM directories ORDER BY owner, path;"
docker compose exec namenode sqlite3 /data/namenode.db \
  "SELECT path, size FROM files WHERE committed=1;"
docker compose exec namenode sqlite3 /data/namenode.db \
  "SELECT b.block_id, b.seq, f.path, group_concat(bl.datanode_id) \
   FROM blocks b \
   JOIN files f ON f.id=b.file_id \
   LEFT JOIN block_locations bl ON bl.block_id=b.block_id \
   GROUP BY b.block_id;"
```

### 4.2 Nativo (Python local, `.test/namenode.db`)

```bash
# Host
sqlite3 .test/namenode.db ".tables"
sqlite3 .test/namenode.db "SELECT owner, path FROM directories ORDER BY owner, path;"
sqlite3 .test/namenode.db "SELECT path, size FROM files WHERE committed=1;"
sqlite3 .test/namenode.db "SELECT b.block_id, b.seq, f.path, group_concat(bl.datanode_id) FROM blocks b JOIN files f ON f.id=b.file_id LEFT JOIN block_locations bl ON bl.block_id=b.block_id GROUP BY b.block_id;"
```

### 4.3 Terraform / AWS

La BD está en el volumen del contenedor `namenode` dentro de la EC2:

```bash
# Host
ssh -i <key.pem> ubuntu@<ip-namenode> "docker exec namenode sqlite3 /data/namenode.db '.tables'"
ssh -i <key.pem> ubuntu@<ip-namenode> "docker exec namenode sqlite3 /data/namenode.db \
  'SELECT path, size FROM files WHERE committed=1;'"
```
