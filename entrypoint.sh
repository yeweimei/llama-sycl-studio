#!/bin/bash
# entrypoint.sh - 单容器入口：同时拉起 llama-server router 和 WebUI
set -e

# ========== 环境变量 ==========
ROUTER_PORT="${ROUTER_PORT:-8070}"
WEBUI_PORT="${WEBUI_PORT:-9100}"
MODELS_DIR="${MODELS_DIR:-/models}"
MODELS_MAX="${MODELS_MAX:-3}"
LLAMA_SERVER="/app/llama-server"
STUDIO_DIR="/app/studio"

# SYCL 环境变量
export ZES_ENABLE_SYSMAN="${ZES_ENABLE_SYSMAN:-1}"
export GGML_SYCL_ENABLE_FLASH_ATTN="${GGML_SYCL_ENABLE_FLASH_ATTN:-1}"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/app}"

# 代理（容器内 pip/下载用）
if [ -n "$HTTP_PROXY" ] || [ -n "$http_proxy" ]; then
    export http_proxy="${http_proxy:-$HTTP_PROXY}"
    export https_proxy="${https_proxy:-$HTTPS_PROXY}"
fi

echo "⬢ llama-studio 单容器启动"
echo "  Models:    ${MODELS_DIR}"
echo "  Router:    0.0.0.0:${ROUTER_PORT}"
echo "  WebUI:     0.0.0.0:${WEBUI_PORT}"
echo "  GPU:       $(ls /dev/dri/ 2>/dev/null || echo 'none')"
echo "  MODELS_MAX: ${MODELS_MAX}"

# ========== 启动 llama-server (router mode) ==========
echo "⬢ 启动 llama-server router..."

# 构建 router 启动参数
ROUTER_ARGS=(
    --models-dir "${MODELS_DIR}"
    --models-max "${MODELS_MAX}"
    --embeddings
    -c 8192
    --flash-attn on
    --jinja
    --host 0.0.0.0
    --port "${ROUTER_PORT}"
)

# 如果有 config.ini，加 --models-preset
if [ -f "${MODELS_DIR}/config.ini" ]; then
    echo "  发现 config.ini，启用 --models-preset"
    ROUTER_ARGS+=(--models-preset "${MODELS_DIR}/config.ini")
fi

"${LLAMA_SERVER}" "${ROUTER_ARGS[@]}" &
ROUTER_PID=$!
echo "  Router PID: ${ROUTER_PID}"

# 等待 router 就绪
echo "⬢ 等待 router 就绪..."
for i in $(seq 1 60); do
    if curl -s "http://127.0.0.1:${ROUTER_PORT}/health" > /dev/null 2>&1; then
        echo "  Router 就绪 (${i}s)"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "  ⚠ Router 60s 内未就绪，继续启动 WebUI..."
    fi
    sleep 1
done

# ========== 启动 WebUI ==========
echo "⬢ 启动 WebUI..."
cd "${STUDIO_DIR}/backend"
export LLAMA_MODEL_DIR="${MODELS_DIR}"
export LLAMA_ROUTER_URL="http://127.0.0.1:${ROUTER_PORT}"
export WEBUI_PORT="${WEBUI_PORT}"

python3 run.py &
WEBUI_PID=$!
echo "  WebUI PID: ${WEBUI_PID}"

# ========== 优雅退出 ==========
trap 'echo "⬢ 收到终止信号，正在关闭..."; kill -TERM $ROUTER_PID $WEBUI_PID 2>/dev/null; wait $ROUTER_PID $WEBUI_PID 2>/dev/null; echo "⬢ 已关闭"; exit 0' TERM INT

echo "⬢ 所有服务已启动"
echo "  WebUI:  http://0.0.0.0:${WEBUI_PORT}"
echo "  API:    http://0.0.0.0:${WEBUI_PORT}/v1/*"

# 等待子进程
wait
