# llama-sycl-studio — LLM 推理服务管理台（单容器一体化）

在 Intel GPU（SYCL）上跑 llama.cpp router mode + WebUI 控制台的一体化管理平台。
**单容器**打包推理引擎与前端控制台，一条 `docker run` 可移植到任何有 Intel GPU 的机器。

## 架构

```
┌── 单容器 llama-studio ──────────────────────────┐
│  http://<host>:9100   （唯一入口）               │
│    ├── /        WebUI 管理台（登录保护）          │
│    ├── /v1/*    OpenAI 兼容 API（按模型名路由）   │
│    └── 内部 llama-server router (127.0.0.1:8070) │
│         ├── 多模型按需加载 + LRU 卸载             │
│         ├── 模型目录 /models（宿主挂载）          │
│         └── 预设 config.ini（DB 自动生成）       │
└──────────────────────────────────────────────────┘
```

- **router mode**：llama.cpp 官方路由模式，单端口多模型、按 `model` 字段路由、自动发现 GGUF、按需加载 + LRU 卸载、进程隔离
- **认证**：WebUI 全站登录保护；`/v1/chat/completions`、`/v1/embeddings` 等 API 也需 token（`/v1/models` 公开）
- **持久化**：SQLite DB（密码/预设/任务）在 named volume `llama-studio-data:/root/.llama-studio`，`docker rm` 不丢

## 功能

- **模型池管理**：加载/卸载模型、实时状态（unloaded/loading/loaded）、按需加载
- **服务管理**：模型注册/编辑/启停/重启/删除全生命周期管理，启动时行内展开实时进度条 + 日志滚动
- **统一 API**：`/v1/chat/completions`、`/v1/embeddings`、`/v1/completions`（OpenAI 兼容，SSE 流式）
- **模型预设**：每模型独立参数（ctx/temp/threads/cache-type/flash-attn/jinja 等），CRUD 自动同步 config.ini
- **模型下载**：对接 HuggingFace / ModelScope，断点续传、暂停/继续/重试/删除、实时进度
- **GPU 监控**：xpu-smi 结构化采集（显存/功耗/进程级占用），推理活跃度（吞吐 tok/s）
- **系统监控**：内存、磁盘、模型目录
- **参数模板**：常用配置一键保存/套用
- **移动端适配**：375px 无横向溢出，汉堡菜单 + 抽屉侧边栏，响应式布局

## 技术栈

- 推理：llama.cpp `server-intel` 镜像（SYCL，Intel GPU）
- 后端：Python FastAPI + SQLite（HTTP 控制 router，无需 docker.sock）
- 前端：Vue 3 + Vite + Element Plus（打包进镜像）
- 监控：Intel xpu-smi（容器内，类 nvidia-smi）

## 目录结构

```
llama-sycl-studio/
├── Dockerfile            # 单容器镜像（server-intel 基础 + Python + 前端 dist）
├── entrypoint.sh         # 启动 router (:8070) + WebUI (:9100)
├── backend/
│   ├── main.py           # FastAPI 入口（含 /v1/* 反代）
│   ├── run.py            # 启动脚本
│   └── app/
│       ├── config.py     # 配置（模型目录/端口/router URL）
│       ├── database.py   # SQLite
│       ├── router_client.py  # HTTP 控制 llama-server router
│       ├── proxy.py      # 下载代理
│       └── routers/      # API 路由（services/presets/downloads/auth/...）
├── frontend/             # Vue3 源码（构建后打进镜像）
└── scripts/deploy.sh     # 一键构建 + 部署
```

## 一键部署

```bash
# 在目标机器（需 Docker + Intel GPU 驱动）
bash scripts/deploy.sh [ssh-host]     # 默认 nuc12
```

部署脚本会：构建前端 → rsync 代码 → 构建镜像 → 重启容器。

## 手动部署（单容器）

```bash
# 1. 构建镜像（Intel GPU/SYCL 版）
docker build -t llama-studio:latest .

# 2. 运行（以 NUC12 为例，A770M = card1/renderD129）
docker run -d --name llama-studio --restart unless-stopped \
  -p 9100:9100 \
  -v /home/zhangjiyu/models:/models \
  -v llama-studio-data:/root/.llama-studio \
  --device /dev/dri/card1 --device /dev/dri/renderD129 \
  -e ZES_ENABLE_SYSMAN=1 -e GGML_SYCL_ENABLE_FLASH_ATTN=1 \
  llama-studio:latest

# 3. 访问
#    WebUI:      http://<host>:9100
#    OpenAI API: http://<host>:9100/v1   （API Key = WebUI 登录 token）
```

### 挂载与设备说明

| 项 | 值 | 说明 |
|---|---|---|
| `-p 9100:9100` | 对外唯一端口 | WebUI + /v1/* 都从 9100 出 |
| `-v <models>:/models` | 宿主模型目录 | GGUF 文件放这里，router 自动发现 |
| `-v llama-studio-data:/root/.llama-studio` | named volume | SQLite DB（密码/预设/任务）持久化 |
| `--device /dev/dri/card1 /dev/dri/renderD129` | A770M | Intel Arc 独显（NUC12） |
| `ZES_ENABLE_SYSMAN=1` | 必需 | SYCL 设备枚举 |
| `GGML_SYCL_ENABLE_FLASH_ATTN=1` | 推荐 | Flash Attention |

> ⚠️ NUC12 显卡：A770M = `card1/renderD129`（pci-0000:03:00.0），Iris Xe = `card0/renderD128`。**只映射 A770M**。
> 其他 Intel GPU 机器用 `lspci | grep -i vga` 确认设备号后替换。

## 环境变量（backend/app/config.py）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLAMA_MODEL_DIR` | `/models` | 容器内模型目录 |
| `LLAMA_ROUTER_URL` | `http://127.0.0.1:8070` | 内部 router 地址 |
| `LLAMA_STUDIO_DATA` | `/root/.llama-studio` | 容器内 DB 位置 |
| `WEBUI_PORT` | `9100` | WebUI 端口 |
| `ROUTER_PORT` | `8070` | 内部 router 端口 |

## 安全说明

- WebUI 全站登录保护（token 存 localStorage，401 自动跳登录页）
- `/v1/chat/completions`、`/v1/embeddings`、`/v1/completions` 需带 `Authorization: Bearer <token>`（登录后拿）
- `/v1/models` 公开（同 OpenAI 行为）
- 首次访问需设置管理员密码；建议通过 Tailscale 访问或加反代鉴权

## License

MIT
