# Dockerfile - llama-studio 单容器一体化
# llama-server (router mode) + WebUI (FastAPI + Vue) 同容器
FROM ghcr.io/ggml-org/llama.cpp:server-intel

# 系统依赖 + 设置 LD_LIBRARY_PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-venv curl ca-certificates xpu-smi \
    && rm -rf /var/lib/apt/lists/*

# 注：Intel GPU 驱动保持镜像默认（26.18）——26.27 在 A770M 上引入
# DEVICE_LOST 偶发崩溃（核显显存泄漏 46GB+），回退镜像自带版本
# （ext_intel_free_memory 警告在 26.27 也未解决，回退无损失）

WORKDIR /app/studio

# 拷贝后端
COPY backend/ /app/studio/backend/

# 拷贝前端构建产物
COPY frontend/dist/ /app/studio/frontend/dist/

# 拷贝入口脚本
COPY entrypoint.sh /app/studio/entrypoint.sh
RUN chmod +x /app/studio/entrypoint.sh

# 安装 Python 依赖（用清华镜像源，NUC12 直连 pypi 不稳定）
RUN pip3 install --no-cache-dir --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /app/studio/backend/requirements.txt

# 环境变量默认值
ENV LLAMA_MODEL_DIR=/models \
    LLAMA_ROUTER_URL=http://127.0.0.1:8070 \
    WEBUI_PORT=9100 \
    ROUTER_PORT=8070 \
    MODELS_MAX=3 \
    ZES_ENABLE_SYSMAN=1 \
    GGML_SYCL_ENABLE_FLASH_ATTN=1

# 暴露 WebUI 端口（router 在容器内部，不对外）
EXPOSE 9100

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -s http://127.0.0.1:9100/api/health || exit 1

# 入口（清除原镜像 ENTRYPOINT）
ENTRYPOINT []
CMD ["bash", "/app/studio/entrypoint.sh"]
