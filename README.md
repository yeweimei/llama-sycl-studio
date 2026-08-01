# llama-sycl-studio — LLM 推理服务管理台

管理 NUC12 上 llama.cpp SYCL Docker 推理服务的 WebUI。

## 功能

- **服务管理**：创建/启动/停止/重启推理服务（llama-server 容器），图形化设定推理参数，实时日志
- **参数双向同步**：图形表单 ↔ 命令行文本实时联动
- **模型中心**：扫描本地模型目录（GGUF 头解析：架构/量化/大小）
- **模型下载**：对接 HuggingFace / ModelScope，选文件、断点续传、实时进度
- **API 管理**：OpenAI 兼容端点（自动 `--api-key`），API Key 生成/轮换
- **系统监控**：GPU 状态（xpu-smi）、内存、磁盘、模型目录
- **参数模板**：常用配置一键保存/套用
- **镜像管理**：llama.cpp 官方 SYCL 镜像版本查询

## 技术栈

- 后端：Python FastAPI + docker SDK + SQLite
- 前端：Vue 3 + Vite + Element Plus

## 目录结构

```
llama-sycl-studio/
├── backend/
│   ├── main.py            # FastAPI 入口
│   ├── run.py             # 启动脚本
│   ├── app/
│   │   ├── config.py      # 配置（模型目录/镜像/端口）
│   │   ├── database.py    # SQLite
│   │   ├── docker_mgr.py  # 容器生命周期管理
│   │   └── routers/       # API 路由
│   └── requirements.txt
├── frontend/
│   └── src/views/         # 页面
├── scripts/               # 部署脚本
└── docs/
```

## 本地开发

```bash
# 后端
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run.py              # http://localhost:9000

# 前端（开发模式，vite 代理到 9000）
cd frontend
npm install
npm run dev                # http://localhost:5173
```

## 部署到 NUC12

```bash
# 1. 传输代码
rsync -a --exclude venv --exclude node_modules --exclude dist \
  ~/projects/llama-sycl-studio/ nuc12:~/projects/llama-sycl-studio/

# 2. NUC12 上安装后端依赖
cd ~/projects/llama-sycl-studio/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. 构建前端（本机构建后传输 dist）
cd ~/projects/llama-sycl-studio/frontend && npm run build
rsync -a dist/ nuc12:~/projects/llama-sycl-studio/frontend/dist/

# 4. 启动（NUC12）
cd ~/projects/llama-sycl-studio/backend
source venv/bin/activate
nohup python run.py > /tmp/studio.log 2>&1 &
# 或：scripts/deploy.sh 一键完成
```

## 配置（backend/app/config.py）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLAMA_MODEL_DIR` | `/home/zhangjiyu/models` | 模型目录（NUC12） |
| `LLAMA_STUDIO_DATA` | `~/.llama-studio` | 数据库位置 |
| `webui_port` | 9000 | WebUI 端口 |
| `gpu_devices` | card1/renderD129 | A770M 设备（NUC12） |

> ⚠️ NUC12 上 A770M 是 `card1/renderD129`（pci-0000:03:00.0），Iris Xe 是 `card0/renderD128`。只映射 A770M。

## 安全说明

- 服务 API Key 由 WebUI 生成（`sk-llm-*`），启动容器时注入 `--api-key`
- CORS 当前放开（生产可收紧）
- WebUI 自身建议通过 Tailscale 访问，或加反向代理鉴权
