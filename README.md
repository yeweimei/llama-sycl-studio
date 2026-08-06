<div align="center">

# 🚀 llama-sycl-studio

**Intel GPU 上的本地 LLM 推理服务管理平台 · 单容器一体化**

在 Intel Arc 显卡上跑 llama.cpp（SYCL 后端），提供**模型池管理 + OpenAI 兼容 API + 可观测性 + 自愈**的一站式 Web 控制台。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/yeweimei/llama-sycl-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/yeweimei/llama-sycl-studio/actions/workflows/ci.yml)
[![GitHub stars](https://img.shields.io/github/stars/yeweimei/llama-sycl-studio?style=social)](https://github.com/yeweimei/llama-sycl-studio)

**一条 `docker run` 即可拥有完整的本地 LLM 服务** —— 无需 docker.sock、无需多容器编排、无需 N 个端口。

</div>

---

## ✨ 为什么选 llama-sycl-studio？

| 💎 能力 | 说明 |
|---|---|
| **⚡ Intel GPU 原生加速** | llama.cpp SYCL 后端，A770M / Arc 全系 / 核显均支持，Flash Attention + XMX 引擎 |
| **🚀 MTP 投机解码** | 面板一键开启多 token 预测，实测推理 **+35% 吞吐**（34 t/s），支持预测长度调优 |
| **🧠 MoE 专家 offload** | 专家权重放 CPU、attention 全 GPU，MoE 大模型（如 Qwen3.6-35B-A3B）显存省 2.5GB + 提速 24% |
| **📦 单容器一体化** | 推理引擎 + WebUI + 管理 API 一个容器，一条命令部署，可移植到任何 Intel GPU 机器 |
| **🛡️ 自愈体系** | 孤儿实例检测、僵尸进程收割、失败退避重试、启动保护窗口——模型实例**挂了自动拉起** |
| **🔄 引擎热升级** | 面板内一键升级/回滚 llama.cpp 二进制（自动备份、原子替换、失败回滚）|
| **🔧 模型生命周期管理** | 注册/启停/重启/删除，启动进度条 + 实时日志滚动，按需加载 + LRU 卸载 |
| **📊 可观测性** | GPU 显存/功耗/进程级监控、推理吞吐（tok/s）、API 调用统计、趋势看板 |
| **🖥️ 多模态 + 工具调用** | OpenAI 兼容 `/v1/chat/completions`，支持多模态识图、Function Call、SSE 流式 |
| **📱 移动端适配** | 375px 无横向溢出，随时手机管理模型 |

---

## 🏗️ 架构

```
┌── 单容器 llama-studio ──────────────────────────────┐
│  http://<host>:9100   （唯一入口）                    │
│    ├── /        WebUI 管理台（登录保护）              │
│    ├── /v1/*    OpenAI 兼容 API（按模型名路由）       │
│    └── 内部 llama-server (per-model 实例)            │
│         ├── 每模型独立实例 + 独立参数预设             │
│         ├── 自愈监控（孤儿/僵尸/失败重试）            │
│         ├── 模型目录 /models（宿主挂载）              │
│         └── SQLite DB（预设/密钥/统计，named volume） │
└──────────────────────────────────────────────────────┘
```

- **per-model 实例模式**：每个模型独立 llama-server 进程，ctx/参数各自独立，互不干扰
- **OpenAI 兼容**：`/v1/chat/completions`、`/v1/embeddings`、`/v1/completions`，OpenClaw / LangChain / 任意 OpenAI SDK 直连
- **持久化**：`llama-studio-data:/root/.llama-studio`，`docker rm` 不丢

---

## 🚀 快速部署启动

### 方式一：一键部署脚本（推荐）

```bash
git clone https://github.com/yeweimei/llama-sycl-studio.git
cd llama-sycl-studio
bash scripts/deploy.sh [ssh-host]   # 默认 nuc12，自动构建 + 部署
```

### 方式二：手动 docker run（通用）

```bash
docker build -t llama-studio:latest .

# 以 Intel Arc A770M 为例（其他卡用 lspci | grep -i vga 确认设备号）
docker run -d --name llama-studio --restart unless-stopped \
  -p 9100:9100 \
  -v /path/to/models:/models \
  -v llama-studio-data:/root/.llama-studio \
  --device /dev/dri/card1 --device /dev/dri/renderD129 \
  -e ZES_ENABLE_SYSMAN=1 \
  -e GGML_SYCL_ENABLE_FLASH_ATTN=1 \
  llama-studio:latest
```

### 启动后三步

```bash
# 1. 访问 WebUI（首次设置管理员密码）
open http://<host>:9100

# 2. 把 GGUF 模型放进挂载目录，WebUI 自动发现 → 注册服务
cp my-model.gguf /path/to/models/

# 3. 调用 OpenAI 兼容 API
curl http://<host>:9100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{"model": "my-model", "messages": [{"role": "user", "content": "你好"}]}'
```

> 🔑 API Key = WebUI 登录 token；`/v1/models` 公开（同 OpenAI 行为）。

---

## 🔌 集成生态

- **OpenClaw / 各类 Agent**：配 `baseUrl=http://<host>:9100/v1` + token 即用，工具调用（Function Call）原生支持
- **TDAI / 记忆系统**：本地 LLM 提取完全可用（已验证 L1/L2/L3 全链路）
- **多模态**：mmproj 自动关联，图片输入即用
- **Embedding**：一键注册 embedding 模型（自动加 `--embeddings`）

---

## 🛠️ 功能清单

- **模型池**：加载/卸载、实时状态（unloaded/loading/loaded）、按需加载 + LRU 卸载
- **服务管理**：模型全生命周期（注册/编辑/启停/重启/删除），启动进度条 + 日志滚动
- **模型预设**：每模型独立参数（ctx/temp/threads/cache-type/flash-attn/jinja/**MTP 投机解码**/**MoE 专家 offload** 等）
- **显存估算**：参数设置后一键预测 GPU 占用（llama.cpp 口径，误差 <10%）
- **模型下载**：HuggingFace / ModelScope，断点续传、暂停/继续/重试、实时进度
- **GPU 监控**：xpu-smi 采集（显存/功耗/进程占用），推理吞吐 tok/s
- **引擎管理**：llama.cpp 版本列表/升级/回滚（自动备份 + 失败自动恢复）
- **API 统计**：按模型聚合调用次数/token/耗时可视化
- **聊天测试台**：Markdown、思考过程展示、多会话、多模态图片、流式打断
- **参数模板**：常用配置一键保存/套用
- **模型标签**：自动打标（思考/多模态/MoE/Embedding）+ 自定义
- **移动端适配**：375px 响应式，汉堡菜单 + 抽屉

---

## 📦 技术栈

| 层 | 技术 |
|---|---|
| 推理引擎 | llama.cpp `server-intel` 镜像（SYCL，Intel GPU）|
| 后端 | Python FastAPI + SQLite |
| 前端 | Vue 3 + Vite + Element Plus |
| 监控 | Intel xpu-smi（容器内，类 nvidia-smi）|

## 📁 目录结构

```
llama-sycl-studio/
├── Dockerfile            # 单容器镜像（server-intel 基础 + Python + 前端 dist）
├── entrypoint.sh         # 启动 WebUI (:9100) + 实例管理
├── backend/
│   ├── main.py           # FastAPI 入口（含 /v1/* 反代 + 鉴权）
│   └── app/
│       ├── instance_mgr.py   # 实例管理核心（自愈/参数构建）
│       ├── self_heal.py      # 自愈体系
│       ├── alert.py          # 飞书告警
│       └── routers/          # services/presets/engine/stats/gpu/...
├── frontend/             # Vue3 源码
├── docs/
│   ├── BUILD.md          # 📦 完整构建说明（从零到部署）
│   └── ROADMAP.md        # 路线图（v0.8 健康内核 → v0.9 可观测性 → v1.0 显存估算）
└── scripts/deploy.sh     # 一键构建 + 部署
```

## 📖 文档

- [📦 BUILD.md](docs/BUILD.md) — 从零构建与部署（前置条件/构建/运行/排障）
- [🗺️ ROADMAP.md](docs/ROADMAP.md) — 版本路线图
- [🛡️ PROJECT-STATE.md](docs/PROJECT-STATE.md) — 项目状态档案

## ⚠️ 注意事项

- **显卡设备号**：不同 Intel GPU 设备号不同，`lspci | grep -i vga` 确认后替换 `--device`
- **驱动版本**：A770M 建议驱动 26.18（26.27 会触发 IGC 编译 flash-attn 内核崩溃）
- **内核编译缓存**：建议挂 `llama-studio-cache:/root/.cache` 持久化编译缓存（重建后首推 1.3s vs 90s）
- 生产建议通过 Tailscale / 反代访问并加鉴权

## License

MIT © [yeweimei](LICENSE)
