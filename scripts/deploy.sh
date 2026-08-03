#!/bin/bash
# llama-sycl-studio 一键部署（单容器一体化架构）
# 用法: bash scripts/deploy.sh [ssh-host] [--rebuild]
#   - 默认目标 nuc12
#   - --rebuild: 强制重新构建镜像（否则仅当镜像不存在时构建）
set -e

TARGET="${1:-nuc12}"
REBUILD=false
for a in "$@"; do [ "$a" = "--rebuild" ] && REBUILD=true; done
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "⬢ 部署 llama-sycl-studio -> ${TARGET}"

# 1. 构建前端（产物打进镜像）
echo "[1/4] 构建前端..."
cd "$ROOT/frontend"
npm run build

# 2. 传输代码（排除本地依赖）
echo "[2/4] 传输代码..."
rsync -a --delete \
  --exclude backend/venv --exclude backend/__pycache__ \
  --exclude frontend/node_modules --exclude frontend/src \
  --exclude .git \
  "$ROOT/" "${TARGET}:~/projects/llama-sycl-studio/"

# 3. 构建镜像
echo "[3/4] 构建镜像 llama-studio:latest ..."
ssh "$TARGET" "cd ~/projects/llama-sycl-studio && \
  if [ '$REBUILD' = 'true' ] || ! docker image inspect llama-studio:latest >/dev/null 2>&1; then \
    docker build -t llama-studio:latest .; \
  else \
    echo '  镜像已存在，跳过构建（--rebuild 强制重建）'; \
  fi"

# 4. 重启容器（保留数据卷 llama-studio-data）
echo "[4/4] 重启容器..."
ssh "$TARGET" "docker rm -f llama-studio 2>/dev/null || true; \
  docker volume create llama-studio-data >/dev/null 2>&1 || true; \
  docker run -d --name llama-studio --restart unless-stopped \
    -p 9100:9100 \
    -v /home/zhangjiyu/models:/models \
    -v llama-studio-data:/root/.llama-studio \
    --device /dev/dri/card0 --device /dev/dri/renderD128 \
    --device /dev/dri/card1 --device /dev/dri/renderD129 \
    -e ZES_ENABLE_SYSMAN=1 -e GGML_SYCL_ENABLE_FLASH_ATTN=1 \
    llama-studio:latest && \
  sleep 8 && \
  echo '✅ WebUI: http://\$(hostname -I | awk \"{print \\\$1}\"):9100'"

echo "✅ 部署完成"
