#!/bin/bash
# entrypoint.sh - 单容器入口：同时拉起 llama-server router 和 WebUI
set -e

# ========== 环境变量 ==========
ROUTER_PORT="${ROUTER_PORT:-8070}"
WEBUI_PORT="${WEBUI_PORT:-9100}"
MODELS_DIR="${MODELS_DIR:-/models}"
MODELS_MAX="${MODELS_MAX:-3}"
# router 全局上下文预算：0 = 从各模型 preset 的 ctx-size 加载（推荐，
# 支持每个模型单独控制上下文）；>0 时钳制所有子模型 ctx 为此值
ROUTER_CTX="${ROUTER_CTX:-0}"
LLAMA_SERVER="/app/llama-server"
STUDIO_DIR="/app/studio"

# SYCL 环境变量
export ZES_ENABLE_SYSMAN="${ZES_ENABLE_SYSMAN:-1}"
export GGML_SYCL_ENABLE_FLASH_ATTN="${GGML_SYCL_ENABLE_FLASH_ATTN:-1}"
# 动态收集 Intel oneAPI lib 路径（镜像版本升级后路径可能变化，运行时收集最可靠）
ONEAPI_LIBS=$(find /opt/intel/oneapi -type d \( -name lib -o -name lib64 \) 2>/dev/null | paste -sd: || true)
export LD_LIBRARY_PATH="${ONEAPI_LIBS:+${ONEAPI_LIBS}:}/app"

# 代理（容器内 pip/下载用）
if [ -n "$HTTP_PROXY" ] || [ -n "$http_proxy" ]; then
    export http_proxy="${http_proxy:-$HTTP_PROXY}"
    export https_proxy="${https_proxy:-$HTTPS_PROXY}"
fi

echo "⬢ llama-studio 单容器启动"
echo "  Models:    ${MODELS_DIR}"
echo "  Router:    0.0.0.0:${ROUTER_PORT}"
echo "  WebUI:     0.0.0.0:${WEBUI_PORT}"
DRI_DEVICES=$(ls /dev/dri/ 2>/dev/null | grep -E '^(card|renderD)' | tr '\n' ' ' || true)
echo "  GPU:       ${DRI_DEVICES:-none（CPU 模式）}"
echo "  MODELS_MAX: ${MODELS_MAX}"

# 检测可用 GPU 数量（/dev/dri/card*），无 GPU 时 router 以 CPU 模式运行
GPU_COUNT=$(ls /dev/dri/card* 2>/dev/null | wc -l)
export GPU_COUNT

# ========== 启动前：从 DB 重建 config.ini（保证重启后预设生效）==========
echo "⬢ 重建 config.ini（从 DB 预设）..."
cd "${STUDIO_DIR}/backend"
export PYTHONPATH="${STUDIO_DIR}/backend"
export LLAMA_MODEL_DIR="${MODELS_DIR}"
export LLAMA_STUDIO_DATA="${LLAMA_STUDIO_DATA:-/root/.llama-studio}"
python3 -c "from app.routers.presets import _write_config_ini; r = _write_config_ini(); print('  config.ini:', 'OK' if r.get('ok') else 'SKIP/ERR', r.get('path',''))" 2>&1 | tail -2
cd "${STUDIO_DIR}"

# ========== 启动前：从卷激活引擎版本（升级成果跨重建持久化）==========
DATA_DIR="${LLAMA_STUDIO_DATA:-/root/.llama-studio}"
BIN_DIR="${DATA_DIR}/bin"
ACTIVE_VERSION=""
if [ -f "${BIN_DIR}/active_version" ]; then
    ACTIVE_VERSION="$(cat "${BIN_DIR}/active_version" | tr -d '[:space:]')"
fi
if [ -n "${ACTIVE_VERSION}" ] && [ -d "${BIN_DIR}/${ACTIVE_VERSION}" ] && [ -e "${BIN_DIR}/${ACTIVE_VERSION}/llama-server" ]; then
    echo "⬢ 激活卷内引擎版本: ${ACTIVE_VERSION}（从 ${BIN_DIR}/${ACTIVE_VERSION} 恢复到 /app/）"
    cp -a "${BIN_DIR}/${ACTIVE_VERSION}/." /app/
else
    echo "⬢ 无卷内激活版本，使用镜像内置引擎（b387 默认）"
fi

# ========== 启动 llama-server (router mode) ==========
echo "⬢ 启动 llama-server router..."

# 构建 router 启动参数
ROUTER_ARGS=(
    --models-dir "${MODELS_DIR}"
    --models-max "${MODELS_MAX}"
    --embeddings
    --metrics
    -c "${ROUTER_CTX}"
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

DATA_DIR="${LLAMA_STUDIO_DATA:-/root/.llama-studio}"
LOG_FILE="${DATA_DIR}/router.log"
mkdir -p "${DATA_DIR}"

# 启动 llama-server，stdout/stderr 经 while read 加 ISO 时间戳前缀后写入 router.log
# （llama.cpp 原生日志是相对时间戳 0.00.859.603，无法按时间过滤，加前缀后支持 since/until）
# 注：用 while read 而非 awk —— mawk 对非 tty 输出全缓冲，fflush 不生效导致日志写不出去
"${LLAMA_SERVER}" "${ROUTER_ARGS[@]}" > >(while IFS= read -r line; do echo "$(date '+%Y-%m-%d %H:%M:%S') ${line}"; done > "${LOG_FILE}") 2>&1 &
ROUTER_PID=$!
echo "  Router PID: ${ROUTER_PID}"
echo "  Log file:   ${LOG_FILE}"

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
export LLAMA_STUDIO_DATA="${LLAMA_STUDIO_DATA:-/root/.llama-studio}"
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
