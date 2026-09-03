# llama-sycl-studio 项目状态档案

> **用途**：新会话接手的唯一入口。开发前必读，开发后必更新。
> **⚠️ 开发前必读 `docs/DEV-NOTES.md`**（踩坑沉淀的开发注意事项：参数面板三处同步/空字符串 422/部署验证等）。
> **维护**：每次有架构/部署/关键参数变更时更新本节「最近变更」。
> 详细构建见 `BUILD.md`，路线图见 `ROADMAP.md`。

## 一、项目是什么

本地 LLM 推理服务管理平台（llama.cpp + SYCL + Intel Arc GPU），单容器部署。
- 仓库：`~/projects/llama-sycl-studio`（远程：github.com/yeweimei/llama-sycl-studio，公开）
- 部署目标：NUC12（192.168.3.246），容器 `llama-studio`，WebUI `:9100`
- 技术栈：后端 Python FastAPI + SQLite（`/root/.llama-studio/studio.db`），前端 Vue3 + Element Plus
- 容器内路径：backend `/app/studio/backend`，frontend dist `/app/studio/frontend/dist`

## 二、当前模型部署（2026-08-06 状态）

| 服务 id | 模型 | 状态 | 端口 | 说明 |
|---|---|---|---|---|
| 1 | Qwen3-Embedding-0.6B-GGUF | loaded | 8110? | 核显 SYCL1，embedding 专用 |
| 30 | Qwen3.6-35B-A3B-GGUF | 曾 loaded | 8110 | 旧版（已由 MTP 版替代）|
| 31 | Qwen3.5-9B-MTP-GGUF | loaded | 8111 | IQ4_XS 5.1GB，TDAI LLM 专用 |
| 32 | **Qwen3.6-35B-A3B-MTP-GGUF** | loaded | 8112 | **主模型**，14GB + mmproj 861MB |

> ⚠️ 模型目录：`/models/<模型名>/`（NUC12 容器内）。当前有 GGUF 版与 MTP 版两个 Qwen3.6 文件，MTP 版为当前主用。

## 三、Qwen3.6 主模型当前参数（预设 id=16）

```
ctx_size: 131072（131K，刻意保持，勿改）
threads: 8 | parallel: 2 | n_gpu_layers: 32 | device: SYCL0
flash_attn: on | jinja: on | mmap: on
cpu_moe: 1（✅ MoE 专家 offload CPU，attention 全 GPU）
mtp: 1（✅ MTP 投机解码，自投机，无需 mtp_model 路径）
mtp_n_max: 3（预测长度）
```

**实测效果**（Qwen3.6-35B-A3B IQ3_XXS）：
- 纯 28 层 GPU：15.7 t/s，15870MiB（濒临 OOM）
- `--cpu-moe` + threads 18：19.5 t/s（+24%），~13.3GB
- 当前 32 层 + cpu_moe + MTP：显存 ~14.5GB / 16GB（可跑）

**⚠️ 已知问题**：`--cpu-moe` 对 Gated DeltaNet 架构（Qwen3.6）触发一次性 IGC 编译警告（`Error OP MUL` + `fused Gated Delta Net not supported, set to disabled`），**非致命**——llama.cpp 自动 fallback，推理正常。Qwen3.5-9B（标准 MoE）无此问题。

## 四、MTP 投机解码

- llama.cpp b387（10262）支持 `--spec-type draft-mtp`（自投机，模型内置 MTP 头时无需单独文件）
- 参数链：`--spec-type draft-mtp` + `--spec-draft-model <path>`（可选，外部 MTP 文件）+ `--spec-draft-n-max N`（预测长度，默认 3）
- **实测加速**：Qwen3.5-9B-MTP 开 MTP 34.3 t/s vs 关 25.4 t/s（**+35%**）；接受率 65.5%，mean len 2.97
- 前端：ParamForm 有「MTP 投机解码」开关 + 「MTP 模型」路径 + 「预测长度」输入（1-16，默认 3）
- DB 字段：`mtp`、`mtp_model`、`mtp_n_max`（model_presets 表）

## 五、实例管理与自愈体系（v0.8+）

- `backend/app/instance_mgr.py`：实例生命周期核心。启动参数构建 `_build_args()`（含 cpu_moe/mtp 透传）
- 孤儿实例检测（端口/健康探测）→ 僵尸收割（/proc PPID 扫描）→ tini PID 1 → stop 后端口释放
- 自愈：`STARTUP_GRACE_SECONDS=240` 保护窗口 + 失败退避 20s→60s→120s + error 冷却 600s 自动重试
- 内核编译缓存：`llama-studio-cache:/root/.cache` + NEO_CACHE_DIR（重建后首推 1.3s vs 原 90s）
- 显存估算：`/api/presets/estimate-memory`（llama.cpp 口径，误差 <10%）

## 六、网关与代理

- `backend/main.py`：v1 代理/鉴权/后台任务。**⚠️ 含工具 schema pattern 清洗**（c1db157：OpenClaw 调用本地模型时，未锚定 pattern 触发 llama.cpp 400，转发前递归剔除）
- `/v1/models` 返回注册服务列表（OpenClaw 兼容）
- embedding 请求自动加 `--embeddings`
- 飞书告警：`backend/app/alert.py`（M7）

## 七、TDAI 集成（本机）

- **LLM**：`/tmp/tdai-v3-config.yaml`（容器 `tdai-memory-core` 挂载生效）→ `model: Qwen3.5-9B-MTP-GGUF`（**8-6 从 Qwen3.6 切换，MTP 版快 2 倍**）
- **embedding**：本地 Qwen3-Embedding-0.6B（1024 维，不变）
- 容器：`agentmemory/memory-core:l1-timeout-30min`（**当前固化镜像**，L1/L2/persona 超时全 1800s），端口 8420，配置容器内 `/data/config/tdai-gateway.yaml`
- ⚠️ 本机 `~/.openclaw/tdai-gateway.yaml`（systemd 版）**未挂载不生效**，但已同步为 MTP 模型防混淆
- L2 场景提取已稳定工作（thinking 模式 + 30min 超时 + MTP 提速）
- L1-dedup 偶发 `Headers Timeout`（并发抢 slot）→ 降级全部入库，不丢数据
- 改容器源码后**必须 `docker commit` 固化**（容器跑 TS 源码 `node --import tsx src/gateway/server.ts`，改 src 重启即生效，但重建会丢）

## 八、部署

```bash
cd ~/projects/llama-sycl-studio && bash scripts/deploy.sh nuc12 --rebuild
```
- 部署脚本：拉代码 → 构建前端 → docker build → 重启容器
- NUC12 API key 在 `/root/.llama-studio/studio.db` 的 `api_keys` 表（全局 Bearer）
- 本机需 NUC12 SSH（ssh nuc12）+ docker 操作 `sudo -S`（密码 `1995419Zhang@`）

## 九、最近变更（新→旧）

- **2026-09-03**：推理参数面板新增 **CPU MoE 层数**控制（`cpu_moe_layers`，手动控制 MoE 专家层 offload CPU 的层数）。背景：`cpu_moe` 原来只有开/关（全部专家层放 CPU，`--cpu-moe`）；引擎 b10760 支持 `--n-cpu-moe N`（仅前 N 层专家权重放 CPU）。实现遵循 DEV-NOTES §1：三处同步（ParamForm 开关下方条件显示 `cpu_moe_layers` 数字输入 / Services.vue payload 枚举 + DEFAULT_PRESET / presets.py Create+Update+List 全链路 + PG 迁移）+ 双透传（instance_mgr `_build_args` + `_write_config_ini`）。**语义**：`cpu_moe` 主开关关=不启用；开且 `cpu_moe_layers=0/空`=全部专家层放 CPU（`--cpu-moe`，此前行为不变）；开且 `>0`=仅前 N 层（`--n-cpu-moe N`，省显存/平衡速度）。兼容：旧预设 `cpu_moe=1` 无层数 → 仍是 `--cpu-moe`。后端 `_empty_to_none` 容错空串（422 防护）+ 前端 normalize/emit 归一化空串→0。已本地验证 Create/Update 各种入参 + `_build_args` 四种组合（off/all/null/N=4）→ 正确产出 `--cpu-moe`/`--n-cpu-moe 4`，前端 build 通过且 bundle 含「CPU MoE 层数」。**未部署**，需 deploy nuc12 生效。
- **2026-08-26（深夜）**：**监控/统计面板重构**（commit fe08f4f）：① instance_mgr 所有实例加 `--metrics`（暴露 /metrics）② 新增 `/api/perf/instances` 聚合 loaded 实例实时指标：decode/prefill t/s（瞬时 gauge + 累计均值双口径）、**MTP 投机接受率**（spec_decode counters）、请求处理中/排队、累计 tokens ③ Monitor.vue 新增「模型实时性能」表（每模型 decode/prefill/MTP接受率/请求/累计tokens）+「实时趋势」图（吞吐/显存/功耗三模式，5s 采样最近 60 点）④ Stats.vue 模型统计加 Prefill/Decode 平均吞吐列。⚠️ 踩坑：services 表无 port 列（用 `BASE_PORT+sid-1` 计算）；`llamacpp:tokens_predicted_total` 是生成 token 计数器（不是 predicted_tokens_total）。**同日实测**：Qwen3.8-9B decode 24.5 t/s / prefill 15.7 t/s 正确采集；embedding 类模型 decode=0 正常。

- **2026-08-26（晚）**：**模板单套化 + device 语义化 + 监控后端无关化**（commit 2cd519b，用户明确要求：换后端不用重写模板、资源占用不管 Vulkan/SYCL 都要正确显示）。① **模板后端无关**：model_presets 去掉 `backend` 维度（`UNIQUE(model_name)`），旧双后端模板自动合并为单套（每模型保留 updated_at 最新）；`device` 改**语义角色** `auto/discrete/integrated`（不再存 SYCL0/Vulkan1），instance_mgr 新增 `_resolve_device` 按当前引擎动态解析（SYCL→SYCL0/1，Vulkan→Vulkan0/1）；presets/services 归一化同步改语义化。② **监控后端无关**：`/api/gpu` 设备列表改用 `llama-server --list-devices`（主数据源，当前引擎过滤），进程占用按 `--device` 参数归集（SYCLx/Vulkanx 都认），功耗/能耗保留 xpu-smi 按设备角色（Arc=独显/Iris=核显）关联；`selectable` 按当前引擎过滤。③ 前端 Services.vue 设备选择改「自动/独显/核显」语义选项（附当前后端解析到的具体设备名）。**⚠️ 关键发现：b10636 构建同时含 SYCL+Vulkan 双后端**（`--list-devices` 输出 4 个设备），必须按当前引擎过滤/解析。**⚠️ 合并副作用**：Ornith-1.5-35B 模板合并后 mtp=False（保留的是用户最近编辑的 vulkan 套，用户已在 UI 自行调参）。

- **2026-08-26（深夜，显存修复）**：**显存监控用 xpu-smi 真实值覆盖 SYCL 假 free 数据**（commit eb553ae）。SYCL 后端 `--list-devices` 的 free 是假的（SYSMAN 未启用时 `ext_intel_free_memory` 不支持 → use total as free → used 恒为 0），独显占用显示 0。修复：`_xpu_smi_sensors` 解析 memory.used/total，按角色（PCI 00:02.0=核显 / 设备名 Arc=独显）关联后用真实显存覆盖（0<=used<=total 校验；核显共享内存 used>total 丢弃）。⚠️ **角色判定不能依赖设备名**：该 xpu-smi 版本不输出设备名（兜底 "Intel GPU"），必须用 PCI 地址判断。实测：SYCL0 独显 13584/16288 (83%) 与 xpu-smi 原始值一致。

- **2026-08-26（三）**：**修复 Vulkan 设备名被 `_normalize_device` 吞掉导致实例启动报 `invalid device: SYCL0`**（commit d19475e）。根因：切 Vulkan 后端后实例设备必须为 `Vulkan0/Vulkan1`，但 `_normalize_device` 只认 SYCL 前缀，Vulkan 设备名（PUT 时）被强制归一化回 `SYCL0`，DB 永远写不进 Vulkan 设备。修复：原样保留 `SYCL / Vulkan` 前缀。**同时踩坑记录——Ornith-1.5-35B-A3B-MTP-APEX（17.4GB 文件）在 Vulkan1/A770M（16GB）上启动**：① device 必须 Vulkan1 ② 必须开 `cpu_moe=1` 把 MoE 专家权重放 CPU，否则 902MB mmproj 分配 `ErrorOutOfDeviceMemory`（主模型吃掉 ~12GB，剩 4GB 无法连续分配 mmproj）③ ctx 降到 16384 省 KV 显存。生效后 Vulkan1 free ~10GB，MTP + mmproj 均正常加载，用户实测推理 OK。

- **2026-08-26（二）**：**推理后端统一开关 + 双后端参数模板 + 切换联动重启**（commit 3266abe）。① `GET /api/engine/backends` 聚合各后端（激活状态/已装/可用），`POST /api/engine/switch`（version 缺省自动选该后端已装最高版本，本地已有免下载切换）② model_presets 重建表 `UNIQUE(model_name, backend)`（迁移自动归旧数据到 sycl-fp16），**SYCL/Vulkan 各一套推理参数模板**；instance_mgr `_preset_dict` 按当前引擎后端取模板（无则回退 sycl-fp16），`_current_backend()` 读 active_version 反推 ③ Settings.vue 引擎管理顶部「推理后端」radio 统一开关：切换确认弹窗 → 免下载切换 → 提示并联动 `restart-all` 重启全部推理服务；版本管理（已装/可用/回滚）按选中后端维度 ④ Services.vue 推理参数按当前后端保存/加载。⚠️ 用户实战：切换 Vulkan b10631 成功；**Vulkan 后端下需为模型配 Vulkan 模板（device=Vulkan0/1），否则实例启动报 invalid device**（旧 SYCL 模板自动回退但 device 前缀不匹配）。

- **2026-08-26**：**引擎支持 Vulkan 后端构建（flavor 双构建并存 + 免下载切换）**（commit 8057469）。背景：实测 A770M 上 Vulkan vs SYCL（llama-bench，同模型 Q5_K_M 同参数）：短上下文 decode 持平（38.2 vs 36-38 t/s），28K 超长上下文 Vulkan 更好（11.1 vs 5-9 t/s），但 prefill SYCL 快 3-5 倍（1163 vs 224-417 t/s）——整体不换后端，但作为可选能力接入面板。改动：① engine.py `FLAVOR_ASSETS`（sycl-fp16/vulkan 双资产匹配），升级 API 加 `flavor` 参数，备份目录 `vulkan-b{ver}` 与 `b{ver}` 并存，同版本同 flavor 已下载时**免下载直接切换**（`switched: true`）；`_list_local_versions`/`/api/engine/version` 返回 flavor/dir 字段，cleanup 正则兼容 `vulkan-` 前缀；⚠️ 备份目录名用 active 目录（避免 Vulkan 激活时覆盖 SYCL 备份）② instance_mgr/services.py device 归一化支持 Vulkan 前缀 ③ services.py `_device_label_map` + gpu.py `/api/gpu/selectable` 正则兼容 `Vulkan0: name (MiB, MiB free)` 输出，返回 `backend` 字段 ④ Settings.vue 引擎管理：当前版本/已安装版本显示构建标签，可用升级表 SYCL/Vulkan 双按钮，回滚按备份目录名。已验证：API 双构建资产、免下载切换（switched=true）、Vulkan 构建激活后 --list-devices 显示 Vulkan0/1、selectable 返回 Vulkan 设备、Ornith 用 Vulkan1 启动推理正常（decode 26.6 t/s）。⚠️ 注意：**实例设备取 model_presets.device（非 services.gpu_id）**，切 Vulkan 引擎后需把实例 preset 设备改为 Vulkan0/1 才能启动。容器内已有 Vulkan 运行时（mesa intel_icd），无需改镜像。

- **2026-08-25（四）**：**下载完整性校验**（commit 0d6222c + 0a39c78，Ornith 事件后续修复）：① `_download_worker` 完成时校验 `downloaded_bytes == total_bytes`——连接断流导致 read() 静默返回空时不再把截断文件当完成，标记 error「下载不完整: X/Y 字节（保留 .part 可重试续传）」且不 rename ② 修复 Range 被忽略（服务端返回 200 全量而非 206）时错误追加到旧 .part 的问题（改用 wb 从头写）③ download_tasks 补 `error` 列（失败原因持久化，前端可显示；此前仅内存态有）。**实测验证**：本地截断源（发 1/3 断流）→ error + 错误信息 + .part 保留；完整源 → done + 字节精确匹配。⚠️ 测试技巧：worker 走代理（ProxyHandler 无 no_proxy），本地 127.0.0.1 源需临时关 proxy_enabled 才能测。

- **2026-08-25（三）**：**API 统计全面升级**（commit f9c1bdd + 后续修复 5cad591/dc28cfe/941cc43/eef94e2）：① 后端新增**端点维度统计**——`api_request_logs` 加 `endpoint`+`method` 列（建表+ALTER 迁移），v1_proxy 全量埋点（非流式/流式/失败分支/`/v1/models` 分支），services.py chat 记录 `endpoint=/v1/chat/completions`；`api_stats` 补 `ok_count/fail_count`（模型成功率），旧行按 request_count 补齐（迁移兼容）② 新增 `GET /api/stats/endpoints`（按端点聚合：次数/成功率/状态码分布/平均延迟/tokens，支持时间范围）③ 前端 Stats 页全面重构：ECharts 仪表盘（渐变 KPI 卡片、请求趋势三模式切换、状态码环形图、端点统计表带方法徽章/成功率进度条/状态码 tags、模型统计加成功率、最近请求加端点/状态码列、时间范围切换 1h/6h/24h/7d）。⚠️ 踩坑：CREATE INDEX endpoint 在 executescript 里对旧表报 no such column（迁移顺序问题），修复为先 ALTER 加列再建索引。

- **2026-08-25（二）**：功能开发（commit 88f7292）：① 修复 `_get_current_version()` 正则——兼容 launcher 格式 `version: 0.3.0-dev (build 10622)`，不再解析成 b0 ② 新增 `POST /api/engine/cleanup`（保留 active+最近 N 个版本，支持 dry_run 预览；Settings 页「清理旧版本」按钮）③ 新增 `POST /api/services/restart-all` 整体重启总开关（顶栏红色按钮 + 帮助页入口，先全停再逐个启动）④ 新增 `/help` 帮助中心页（引擎/实例/API 快捷命令、升级回滚说明、关键环境变量表、常见问题 FAQ）⑤ UI 美化：深色渐变侧边栏 + 高亮菜单、科技蓝主题色（--el-color-primary 系列）、卡片/表格/对话框圆角、顶栏毛玻璃。已验证：引擎版本正确显示 b10622；cleanup 实际删除 7 个旧版本（保留 b10369/b10437/b10488/b10622）；restart-all 重启 3 个 loaded 实例成功。

- **2026-08-25**：**llama.cpp 正式升级 b10369 → b10622（0.3.0-dev build 10622）**，核显/独显双 GPU 全部可用（commit 56eb393）。背景：b10622 在旧配置下核显 OOM（`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` @ memcpy），排查定位根因 = llama.cpp #26789（commit a97123e4，host pinned memory 默认开启，iGPU 上 memcpy 到设备失败）。**解法：`GGML_SYCL_ENABLE_HOST_PINNED_MEM=0`**（instance_mgr._env() + entrypoint.sh 默认设置，允许环境变量覆盖）。另：驱动已升 26.27（PPA），8-19 的"核显误判 0 内存"问题随之消失；b10488 时代的独显 flash-attn IGC 崩溃在 26.27 下未复现。清理垃圾目录 `bin/b0`（#26789 之前 launcher 版本号解析 bug 产物）与 `bin/unknown`。详见「九.X」更新。

- **2026-08-12**：MTP 投机解码区新增**草稿 KV cache 量化**配置（commit 28b206b）。功能：`spec_draft_type_k/spec_draft_type_v` 两字段（f16/q8_0/q4_0/q4_1/f32 可空，空=默认 f16），对应 `--spec-draft-type-k/v`（别名 `--cache-type-k-draft/v-draft`）。背景：MTP 草稿 KV 默认 f16 浪费显存，35B 主模型显存紧张（13.2/16GB），显式设 q8_0 可省显存（参考 insidentally.com 文章）。实现遵循 DEV-NOTES §1：三处同步（ParamForm MTP 区下拉 + Services payload + presets.py 全链路）+ 双透传（instance_mgr `_build_args` + `_write_config_ini`，仅非空才传）+ DB 建表/ALTER 双路径。**已配置 Qwen3.6-35B-A3B-MTP 主模型（预设 id=16）spec_draft_type_k/v=q8_0 并重启生效**。部署验证：容器 healthy + bundle `Services-D4kuJbFq.js` + 实测 pid 启动命令含 `--spec-draft-type-k q8_0 --spec-draft-type-v q8_0` + config.ini 同步生成。

- **2026-08-12**：推理参数面板新增**思考（Reasoning）**配置（commit 8d88ed5）。功能：`reasoning`（on/off/auto 字符串枚举）+ `reasoning_budget`（整数可空，-1 不限/0 立即结束/N>0 预算）两字段，控制 Qwen 思维链长度（llama.cpp b10369 `--reasoning`/`--reasoning-budget`）。实现遵循 DEV-NOTES §1：三处同步（ParamForm DEFAULT_ARGS+控件+normalize / Services.vue payload 枚举 / presets.py 全链路）+ 双透传（instance_mgr `_build_args` + `_write_config_ini`）+ 开关绑独立布尔字段 `reasoning_enabled`（复用 YaRN 拆字段方案，避免同字段双绑 422）+ 后端 mode='before' validator 容错空串/布尔。**顺带修复**：补齐 database.py 遗漏的 cpu_moe/mtp/mtp_model/mtp_n_max 四列 ALTER 迁移（presets.py INSERT 早已引用但从未加迁移，新库建预设必崩）。部署验证：容器 healthy + bundle hash 更新 + 实际启动 Qwen3.5-9B-MTP 实例，进程 cmdline 含 `--reasoning on --reasoning-budget 1024`；关闭 reasoning='' 重启后参数消失；config.ini 同步生成 reasoning 行。

- **2026-08-12**：推理参数面板新增**长上下文缩放（YaRN/RoPE）**支持 + 修复保存 422 系列问题。功能：`rope_scaling/rope_scale/yarn_orig_ctx` 三字段（DB 建表+ALTER，config.ini + _build_args 双透传）；4 轮修复：① Services.vue payload 漏 rope 字段（0621576）② el-input-number 空字符串 422（3e6ad17/91c5935 全数字字段 mode='before' validator）③ **真凶 el-switch 与 el-select 绑同一字段产生布尔值**（8b849c7，拆 rope_enabled 开关字段 + 后端容错布尔）。坑与手法见 `DEV-NOTES.md` §1.4

- **2026-08-08**：对话日志改进——从独立页（/chat-logs）移入**服务详情页**（ServiceDetail.vue 新增「💬 对话日志」tab），黑底滚动展示与运行日志同风格，**虚拟滚动**（固定行高 52px + 可视区渲染 + 懒渲染 thinking）支持上千条无压力；chat-logs API 支持按模型过滤（?model=）/ 清空（DELETE ?model=）；commit 7d3b644
- **2026-08-08**：对话内容日志——新增 `chat_api_logs` 表（最近 1000 条）+ 记录输入/输出/thinking（/v1/* 代理 main.py + /{sid}/chat services.py 双路径），提交 5241565
- **2026-08-07**：Gemma4-12B 性能优化（threads 8→20，单路 31.5→44.5 tps，并发总吞吐 26→61.6 tps）；MTP 保存 bug 修复（Services.vue/Settings.vue 补 MTP 字段，commit 6f1093d；部署必须 `--rebuild` 否则容器跑旧镜像）；Gemma4 性能基线：单路 44 tps、首 token 54ms、prompt 处理 224 tps
- **2026-08-06**：MTP 预测长度可配置（`--spec-draft-n-max`，commit e98b6d0）；面板 MTP 开关（47765d2）；Qwen3.6 cpu_moe 调优（b5e8ea3）；proxy 工具 schema pattern 清洗（c1db157）；TDAI LLM 切 Qwen3.5-9B-MTP；L1 超时 600s→1800s 固化 `l1-timeout-30min` 镜像；Qwen3.6 主模型切 MTP 版（cpu_moe+mtp 双开）
- **2026-08-05**：Qwen3.6-35B-A3B 部署（IQ3_XXS 13.2GB）；NUC12 端点修复（embedding 501、/v1/models）；驱动回退 26.18（26.27 IGC 编译 flash-attn 崩溃）
- **2026-08-04**：v1.0 显存估算 + 并发控制（M8/M9）；缓存卷持久化；实例管理修复链

## 九.X、llama.cpp 升级陷阱（2026-08-19 排查记录）

**现象**：WebUI「升级 llama.cpp」到最新版（b10488）后，核显/独显跑模型都失败。

**根因**：b10488 新版 SYCL 与 NUC12 当前驱动/oneAPI 组合不兼容，双 GPU 双错：
- **核显 SYCL1**：`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` —— 新版 SYCL `get_memory_info` 读不到核显共享内存（`ext_intel_free_memory is not supported` 警告，SYSMAN 未生效），误判核显 0 可用内存→假 OOM 崩溃
- **独显 SYCL0**：`IGC: Internal Compiler Error ... Error OP FLASH_ATTN_EXT` —— 新版 flash-attn kernel 在当前 26.18 驱动 + oneAPI 2025.3 的 IGC 上编译崩溃（**和 2026-08-05 回退到 26.18 同一类坑**）

**环境**：驱动 intel-opencl-icd 26.18.38308 / level-zero 1.15.38308 / oneAPI 2025.3

**对比实测**（同模型 Qwen3-Embedding-0.6B、同 device、同 SYSMAN=1）：
- b10369：核显 ✅ 正常 listening
- b10488：核显 ❌ OOM 崩溃 / 独显 ❌ IGC flash-attn 崩溃

**PPING 解法**：回滚到 b10369（当前 active 就是它）。**不要升级 b10488**，除非：① 更新 oneAPI（含新 IGC）+ GPU 驱动（>26.18）后，先 `--list-devices` + 核显实测加载通过再启用；② 或单测核显能否用关 flash-attn 绕过（独显关 flash-attn 可能可过，但核显读内存问题仍在，不建议）。

**2026-08-25 更新（b10622 已解禁）**：驱动升到 **26.27**（PPA）后，b10622 的 `get_memory_info` 已能读到核显 free 内存（~12GB），"误判 0 内存"消失；但加载仍 OOM——**新根因 = llama.cpp #26789（commit a97123e4，8-14 合入）host pinned memory**：`sycl::malloc_host` 在 iGPU 上 memcpy 到设备失败（`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY`）。**✅ 解法：`GGML_SYCL_ENABLE_HOST_PINNED_MEM=0`**（默认关闭即可，性能影响可忽略；官方 SYCL.md 已收录该开关）。已实测 b10622 + PINNED=0：核显（Qwen3-Embedding/PaddleOCR）+ 独显（Qwen3.8-9B-Distill）全部正常加载与推理，独显 flash-attn 无 IGC 崩溃。**2026-08-25 已正式切 b10622（active_version=b10622，commit 56eb393）**。

**升级机制备忘**：`POST /api/engine/upgrade` 下载→替换 /app→备份到 `BIN_DIR/{ver}/`→写 `active_version`；**真正生效靠容器重启时 entrypoint `cp -a BIN_DIR/{active}/. /app/`**。升级/回滚后必须**重启容器**才生效。`engine_last_upgrade` 存 epoch 秒，`/root/.llama-studio/bin/active_version` 是当前激活版本。

## 十、下一步待办

- [x] ~~**下载完整性校验**（2026-08-25 Ornith 事件）~~ 已修复（0d6222c + 0a39c78），实测验证通过
- [ ] TDAI L1-dedup Headers Timeout 优化（可选：调大等锁/接受降级）
- [ ] 观察 TDAI L2 提取在 MTP 模型下的长期稳定性
- [ ] 上游 issue/PR：TencentCloud/TencentDB-Agent-Memory（死循环防护 + thinking 适配，issue 草稿 `/tmp/tdai-issue.md`）
