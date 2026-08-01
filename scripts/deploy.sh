#!/bin/bash
# llama-sycl-studio 一键部署到 NUC12
# 用法: bash scripts/deploy.sh [nuc12]
set -e

TARGET="${1:-nuc12}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "⬢ 部署 llama-sycl-studio -> ${TARGET}"

# 1. 构建前端
echo "[1/4] 构建前端..."
cd "$ROOT/frontend"
npm run build

# 2. 传输代码（排除本地依赖）
echo "[2/4] 传输代码..."
rsync -a --delete \
  --exclude backend/venv --exclude backend/__pycache__ \
  --exclude frontend/node_modules \
  "$ROOT/" "${TARGET}:~/projects/llama-sycl-studio/"

# 3. NUC12 安装后端依赖（如 venv 不存在）
echo "[3/4] NUC12 安装后端依赖..."
ssh "$TARGET" 'cd ~/projects/llama-sycl-studio/backend && \
  [ -d venv ] || python3 -m venv venv; \
  source venv/bin/activate && pip install -q -r requirements.txt'

# 4. 重启服务
echo "[4/4] 重启 WebUI..."
ssh "$TARGET" 'pkill -f "python run.py" 2>/dev/null || true; sleep 1; \
  cd ~/projects/llama-sycl-studio/backend && \
  nohup source venv/bin/activate && python run.py > /tmp/studio.log 2>&1 & \
  echo "WebUI 启动: http://$(hostname -I | awk "{print \$1}"):9000"'

echo "✅ 部署完成"
