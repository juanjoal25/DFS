"""Genera un archivo de prueba ("basura") de tamaño parametrizable.

Uso:
    python scripts/gen_testfile.py salida.bin 512MB
    python scripts/gen_testfile.py salida.bin 2GB

Escribe datos pseudoaleatorios en streaming (sin cargar todo en RAM) e
imprime el SHA-256 para verificar integridad tras un get.
"""
import hashlib
import os
import sys

UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
CHUNK = 1024 * 1024  # 1 MB


def parse_size(s: str) -> int:
    s = s.strip().upper()
    for unit in ("GB", "MB", "KB", "B"):
        if s.endswith(unit):
            num = float(s[: -len(unit)])
            return int(num * UNITS[unit])
    return int(s)  # bytes


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    out_path, size_str = sys.argv[1], sys.argv[2]
    total = parse_size(size_str)

    h = hashlib.sha256()
    written = 0
    with open(out_path, "wb") as f:
        while written < total:
            n = min(CHUNK, total - written)
            data = os.urandom(n)
            f.write(data)
            h.update(data)
            written += n
    print(f"Generado {out_path}: {written} bytes ({size_str})")
    print(f"SHA-256: {h.hexdigest()}")


if __name__ == "__main__":
    main()
