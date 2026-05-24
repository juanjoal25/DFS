#!/bin/bash
set -e
apt-get update -y
apt-get install -y docker.io git
systemctl enable --now docker

# IP pública de esta instancia (para que NameNode/cliente la alcancen).
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)

cd /opt
git clone ${git_repo} dfs
cd dfs

docker build -t dfs-datanode -f docker/Dockerfile.datanode .
docker run -d --restart always --name datanode \
  -p 8100:8100 \
  -e DATANODE_ID=${datanode_id} \
  -e NAMENODE_URL=http://${namenode_ip}:8000 \
  -e DATANODE_PUBLIC_URL=http://$${PUBLIC_IP}:8100 \
  -e DATA_DIR=/data/blocks \
  -v /opt/dfs-data:/data \
  dfs-datanode
