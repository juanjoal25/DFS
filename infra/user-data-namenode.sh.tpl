#!/bin/bash
set -e
apt-get update -y
apt-get install -y docker.io git
systemctl enable --now docker

cd /opt
git clone ${git_repo} dfs
cd dfs

docker build -t dfs-namenode -f docker/Dockerfile.namenode .
docker run -d --restart always --name namenode \
  -p 8000:8000 \
  -e BLOCK_SIZE_MB=${block_size_mb} \
  -e REPLICATION_FACTOR=2 \
  -e SEED_USERS="${seed_users}" \
  -e DB_PATH=/data/namenode.db \
  -v /opt/dfs-data:/data \
  dfs-namenode
