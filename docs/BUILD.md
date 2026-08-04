# 构建说明（Build Guide）

本文档说明如何从源码构建 `llama-sycl-studio` 镜像并部署到目标机器。
适用于：**Intel GPU（Arc / Xe）** 主机，需 Docker + Intel GPU 驱动。

---

## 0. 前置条件

| 依赖 | 版本/说明 |
|---|---|
| 操作系统 | Linux（Ubuntu 22.04/24.04 验证过，其他发行版需自测） |
| Intel GPU 驱动 | 需支持 SYCL/Level Zero（Arc A 系列 / Xe 核显） |
| Docker | ≥ 20.10（支持 `--device /dev/dri/*` 直通） |
| Node.js | ≥ 18（仅构建前端时需要，构建镜像的机器上要装） |
| 网络 | 能访问 Docker Hub / ghcr.io（拉基础镜像） |

> 💡 不需要本机有 NVIDIA GPU；本项目专为 **Intel GPU（SYCL）** 设计。

---

## 1. 获取代码

```bash
git clone git@github.com:yeweimei/llama-sycl-studio.git
cd llama-sycl-studio
```

---

## 2. 构建前端（Vue3 → dist）

```bash
cd frontend
npm install          # 安装依赖
npm run build        # 产物输出到 frontend/dist/
cd ..
```

> 产物 `frontend/dist/` 会被 Dockerfile 拷贝进镜像，**必须**先于镜像构建执行。

---

## 3. 构建镜像

```bash
docker build -t llama-studio:latest .
```

### 构建流程拆解（Dockerfile 做了什么）

| 步骤 | 说明 |
|---|---|
| 基础镜像 | `ghcr.io/ggml-org/llama.cpp:server-intel`（官方 SYCL 版，含 llama-server + oneAPI 运行时） |
| 系统依赖 | `python3-pip`、`curl`、`xpu-smi`（Intel GPU 监控，类 nvidia-smi） |
| LD_LIBRARY_PATH | 保留 Intel oneAPI 运行库路径 + 追加 `/app` |
| 拷贝代码 | `backend/`（FastAPI 后端）+ `frontend/dist/`（前端产物）+ `entrypoint.sh` |
| Python 依赖 | 清华镜像源安装 fastapi / uvicorn / httpx 等 |
| 环境变量 | 默认 `LLAMA_MODEL_DIR=/models`、`WEBUI_PORT=9100`、`ROUTER_PORT=8070` 等 |
| 健康检查 | 30s 间隔探测 `/api/health` |

> ⚠️ **镜像体积约 13.8GB** 属正常：基础镜像（llama.cpp SYCL + oneAPI 运行库）占大头，
> 是 Intel GPU 推理镜像的固有体积，非项目冗余。

---

## 4. 运行容器

### 4.1 确认 GPU 设备号

```bash
lspci | grep -i vga
ls -l /dev/dri/
```

> 💡 **自动检测**：`deploy.sh` 会自动扫描目标机 `/dev/dri/*` 并生成 `--device` 直通参数，
> 无需手动确认设备号。以下手动步骤仅用于自定义部署场景。

Intel Arc 独显通常对应 `card1/renderD129`，核显对应 `card0/renderD128`。
**NUC12（A770M）** 用：`/dev/dri/card1` + `/dev/dri/renderD129`（pci-0000:03:00.0）。

### 4.2 启动

```bash
# 自动检测 GPU 设备（推荐）：
DEVICES=$(for d in /dev/dri/card* /dev/dri/renderD*; do echo -n "--device $d "; done)

docker run -d --name llama-studio --restart unless-stopped \
  -p 9100:9100 \
  -v /path/to/your/models:/models \
  -v llama-studio-data:/root/.llama-studio \
  $DEVICES \
  -e ZES_ENABLE_SYSMAN=1 \
  -e GGML_SYCL_ENABLE_FLASH_ATTN=1 \
  llama-studio:latest
```

| 参数 | 说明 |
|---|---|
| `-p 9100:9100` | 对外唯一端口（WebUI + OpenAI API 都从 9100 出） |
| `-v <models>:/models` | 宿主模型目录，GGUF 文件放这里，router 自动发现 |
| `-v llama-studio-data:/root/.llama-studio` | named volume，SQLite DB（密码/预设/任务）持久化 |
| `--device ...` | Intel GPU 设备直通（可自动检测；**无 GPU 机器可不加，自动 CPU 模式**） |
| `ZES_ENABLE_SYSMAN=1` | SYCL 设备枚举必需 |
| `GGML_SYCL_ENABLE_FLASH_ATTN=1` | Flash Attention（推荐） |

> 无 `/dev/dri` 的机器（纯 CPU）：不加 `--device` 即可，llama-server 自动降级 CPU 推理。

### 4.3 验证

```bash
# 容器状态（应显示 healthy，首次需等约 60s 健康检查）
docker ps | grep llama-studio

# WebUI
curl http://127.0.0.1:9100/api/health
# → {"status":"ok","version":"1.0.0"}

# OpenAI 兼容 API（需先登录 WebUI 拿 token）
curl http://127.0.0.1:9100/v1/models
```

浏览器访问 `http://<host>:9100`，首次进入设置管理员密码。

---

## 5. 一键部署脚本（通用任意 Intel 机器）

```bash
# 本地（有 Node + rsync + ssh 免密）一键部署到任意 Intel GPU 机器
bash scripts/deploy.sh [ssh-host] [--rebuild]
# 示例：部署到新机器
bash scripts/deploy.sh 192.168.1.50 --rebuild
```

**脚本自动适配目标机环境：**

| 项 | 自动行为 |
|---|---|
| GPU 设备 | 自动扫描 `/dev/dri/*` 生成 `--device` 直通；无 GPU 则 CPU 模式 |
| 模型目录 | 默认 `~/models`（目标机家目录，适配不同用户）；可用 `MODELS_HOST_DIR` 覆盖 |
| 局域网 IP | 自动探测宿主机 IP 注入容器（接入配置展示用） |
| 端口 | 默认 9100，可用 `WEBUI_PORT` 覆盖 |

**可用环境变量：**

```bash
MODELS_HOST_DIR=/data/models \   # 宿主机模型目录（默认 ~/models）
WEBUI_PORT=9100 \               # WebUI 端口
MODELS_MAX=3 \                  # router 最大驻留模型数
bash scripts/deploy.sh 192.168.1.50
```

脚本流程：构建前端 → rsync 代码 → 远程构建镜像 → 自动探测环境 → 启动容器（保留数据卷）。

---

## 6. 模型准备

1. 下载 GGUF 格式模型（如 Qwen 系列）到宿主机的 `/models` 目录：
   ```bash
   # 例：Qwen3-Embedding-0.6B
   huggingface-cli download Qwen/Qwen3-Embedding-0.6B-GGUF --local-dir /models/qwen3-embedding-0.6b
   ```
2. 也可在 WebUI 的「模型下载」页直接对接 HuggingFace / ModelScope 下载
3. 模型按需加载：WebUI 服务管理页注册 → 启动 → 自动发现并加载

---

## 7. 常见问题

| 问题 | 解决 |
|---|---|
| 容器启动报 `level_zero` 相关错误 | 确认 `--device` 映射了正确的 GPU 设备 + `ZES_ENABLE_SYSMAN=1` |
| GPU 未识别 | `ls /dev/dri/` 检查；NUC12 只映射 A770M（card1），勿混入核显 |
| 镜像构建很慢 | 正常（oneAPI 层大）；可配置 Docker 镜像加速器 |
| pip 安装慢/失败 | Dockerfile 已内置清华源；容器内手动装包时加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 模型不出现 | 确认 `.gguf` 文件在 `/models` 下、WebUI 设置页刷新模型列表 |
| WebUI 忘记密码 | `docker exec llama-studio python3 -c "..."` 重置 DB 中密码，或删除 volume 重新初始化 |
