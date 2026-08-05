#!/bin/bash
# llama-sycl-studio 一键部署（单容器一体化架构，任意 Intel GPU 设备通用）
#
# 用法:
#   bash scripts/deploy.sh [ssh-host] [--rebuild]
#   bash scripts/deploy.sh 192.168.1.50 --rebuild
#
# 可用环境变量（覆盖默认值）:
#   MODELS_HOST_DIR=/path/to/models   宿主机模型目录（默认 ~/models）
#   WEBUI_PORT=9100                   WebUI 端口
#   MODELS_MAX=3                      router 最大驻留模型数
#   CONTAINER_NAME=llama-studio       容器名
#   DATA_VOLUME=llama-studio-data     数据卷名
#
# 特性:
#   - 自动检测目标机 /dev/dri/* 设备并直通容器（无需手动指定 GPU）
#   - 无 GPU 设备时自动以 CPU 模式运行（llama-server 自动降级）
set -e

TARGET="${1:-nuc12}"
REBUILD=false
for a in "$@"; do [ "$a" = "--rebuild" ] && REBUILD=true; done
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ========== 可配置参数（环境变量覆盖） ==========
MODELS_HOST_DIR="${MODELS_HOST_DIR:-}"
WEBUI_PORT="${WEBUI_PORT:-9100}"
MODELS_MAX="${MODELS_MAX:-3}"
ROUTER_CTX="${ROUTER_CTX:-0}"  # 0=各模型 preset 独立控制
CONTAINER_NAME="${CONTAINER_NAME:-llama-studio}"
DATA_VOLUME="${DATA_VOLUME:-llama-studio-data}"

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

# 4. 探测目标机环境（LAN IP + 模型目录 + GPU 设备）
echo "[4/4] 探测目标机环境并启动容器..."
HOST_LAN_IP=$(ssh "$TARGET" "hostname -I | awk '{print \$1}'")
echo "  LAN IP: ${HOST_LAN_IP}"

# 模型目录：未指定时默认 ~/models（目标机家目录，自动适配不同用户）
if [ -z "$MODELS_HOST_DIR" ]; then
  MODELS_HOST_DIR=$(ssh "$TARGET" "echo ~/models")
fi
echo "  模型目录: ${MODELS_HOST_DIR}"
ssh "$TARGET" "mkdir -p '${MODELS_HOST_DIR}'" 2>/dev/null || true

# GPU 设备：自动检测 /dev/dri/*（card + renderD），无 GPU 则跳过（CPU 模式）
DRI_DEVICES=$(ssh "$TARGET" "ls /dev/dri/ 2>/dev/null | grep -E '^(card|renderD)' || true")
DEVICE_ARGS=""
if [ -n "$DRI_DEVICES" ]; then
  for dev in $DRI_DEVICES; do
    DEVICE_ARGS="${DEVICE_ARGS} --device /dev/dri/${dev}"
  done
  echo "  GPU 设备: ${DEVICE_ARGS}"
else
  echo "  ⚠ 未检测到 /dev/dri GPU 设备，将以 CPU 模式运行"
fi

# 5. 启动容器
echo "  启动容器 ${CONTAINER_NAME} ..."
ssh "$TARGET" "docker rm -f ${CONTAINER_NAME} 2>/dev/null || true; \
  docker volume create ${DATA_VOLUME} >/dev/null 2>&1 || true; \
  docker volume create llama-studio-cache >/dev/null 2>&1 || true; \
  docker run -d --name ${CONTAINER_NAME} --restart unless-stopped \
    -p ${WEBUI_PORT}:9100 \
    -e HOST_LAN_IP=${HOST_LAN_IP} \
    -e MODELS_MAX=${MODELS_MAX} \
    -e ROUTER_CTX=${ROUTER_CTX} \
    -v '${MODELS_HOST_DIR}':/models \
    -v ${DATA_VOLUME}:/root/.llama-studio \
    -v llama-studio-cache:/root/.cache \
    -e NEO_CACHE_DIR=/root/.cache/neo_compiler_cache \
    -e NEO_CACHE_PERSISTENT=1 \
    ${DEVICE_ARGS} \
    -e ZES_ENABLE_SYSMAN=1 -e GGML_SYCL_ENABLE_FLASH_ATTN=1 \
    llama-studio:latest && \
  sleep 8 && \
  echo '✅ WebUI: http://\$(hostname -I | awk "{print \\\$1}"):${WEBUI_PORT}'"

echo "✅ 部署完成"
