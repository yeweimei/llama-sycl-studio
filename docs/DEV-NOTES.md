# llama-sycl-studio 开发注意事项（DEV-NOTES）

> **给后续接手的开发智能体**：开发本项目前先读本文件 + `PROJECT-STATE.md`。
> 这些是从真实踩坑中沉淀的硬规则，违反会踩同样的坑。

---

## 0. 开发流程铁律

1. **三端同步闭环**：本机 commit → push GitHub → NUC12 部署（`bash scripts/deploy.sh nuc12 --rebuild`）→ 验证。不留半同步状态。
2. **改前端必须 `npm run build`**：改 `.vue` 源文件 ≠ 用户能看到。build 失败 = 任务未完成。
3. **开发完更新 `PROJECT-STATE.md`**：在「最近变更」追加关键变更（新→旧）。
4. **部署后必须验证产物**：只看容器 healthy 不够。前端查 bundle hash 是否为新（`docker exec llama-studio ls -la /app/studio/frontend/dist/assets/`），后端 grep 关键代码（如 `docker exec llama-studio grep -c 'xxx' /app/studio/backend/app/...py`）。
5. **⚠️ Vite minify 会重命名变量**：在产物 JS 里 grep 源码变量名（如 `onYarnSwitch`、`p.rope_scale`）可能搜不到——minifier 改成了 `o`/`P` 等短名。验证产物时搜**行为痕迹**（如 `.rope_scale=null`、`长上下文缩放` 文案），或对比 bundle 文件名 hash，不要因 grep 不到就误判"没部署"。

---

## 1. 前端参数面板（ParamForm.vue + Services.vue）⚠️ 最容易踩坑

### 1.1 新增参数字段 = 三处都要改，漏一处就"面板设置了不生效"
本项目前端保存 preset 是**显式枚举字段**构造 payload（不是 `...spread`），所以加一个新参数（如 YaRN 的 rope_scaling）必须同步改：

| 位置 | 文件 | 改什么 |
|---|---|---|
| 1 | `components/ParamForm.vue` | DEFAULT_ARGS + 表单控件 + normalize |
| 2 | `views/Services.vue` | 保存 payload 显式枚举里**加字段**（约 L630-640 `const payload = {...}`） |
| 3 | 后端 `routers/presets.py` | PresetCreate/PresetUpdate/insert/update/list 全链路 |

**血泪案例**：YaRN 参数面板能设、但保存后 DB 一直是空——就是漏了 Services.vue 的 payload 枚举（2026-08-11, commit 0621576）。

**自检清单**：加字段后，逐个确认：
- [ ] ParamForm 能显示/输入
- [ ] Services.vue payload 包含它
- [ ] 后端 Create + Update + List 三个接口都处理它
- [ ] DB 迁移（CREATE TABLE + ALTER 双路径）加列
- [ ] `_build_args`（实例模式参数）+ `_write_config_ini`（router 模式 config.ini）**两处透传**——本项目有双生效路径，漏一个就一半场景不生效

### 1.2 el-input-number 清空后值是 `""`（空字符串），不是 null！⚠️
- 用户清空数字输入框后，`v-model` 的值是 **`''`**，不是 `null`/`undefined`。
- 前端 `x ?? null` **不会**兜底 `''`（`'' ?? null` = `''`），必须显式判断：
  ```js
  rope_scale: (p.rope_scale === '' || p.rope_scale === null || p.rope_scale === undefined) ? null : Number(p.rope_scale)
  ```
- 后端 Pydantic 也不能只依赖类型 `float | None`：`''` 会在**类型解析阶段**直接报 `float_parsing` 422，普通 `field_validator`（默认 mode="after"）根本执行不到。
  **必须用 `mode="before"`**，在类型解析前归一化：
  ```python
  @field_validator("rope_scale", "yarn_orig_ctx", mode="before")
  @classmethod
  def _empty_to_none(cls, v):
      if v == "" or v is None:
          return None
      return v
  ```
- 原则：**数值可空字段，前端归一化 + 后端 mode="before" 双保险**，这样不依赖用户清浏览器缓存（旧 JS 仍会发 `''`）。

### 1.3 开关联动清空
- el-switch 的 `@change` 回调参数是**切换后的值**（`inactive-value` 时关闭为 falsy）。
- 开关关闭时要联动清空关联字段（如关 YaRN 清 scale/orig），否则残留旧值保存进 DB，面板重开显示脏数据。

---

## 2. 后端 API（FastAPI + SQLite）

1. **DB 迁移双路径**：`database.py` 的 CREATE TABLE（新库）+ ALTER 迁移区（老库）都要加列，用 `if "col" not in cols` 防重复。
2. **区分「不修改」和「清空」**：`None` 默认值 = 前端没传（不修改）；但 `''`/`0` 可能是前端要的清空/合法值。Pydantic model 里 `str | None = None` 无法区分"没传"和"传了 null"，按字段语义设计（参考 rope_scaling 用 `is not None` 判断、`''` 触发联动清空）。
3. **测试 422 行为**：改 Pydantic model 后，本地起 python 模拟（不要只靠 curl 到 NUC12），确认空串/null/0/正常值四种输入都符合预期。

---

## 3. 部署（deploy.sh nuc12 --rebuild）

1. 部署前确认本地 `git log --oneline -1` 是目标 commit。
2. NUC12 docker 免 sudo，ssh nuc12 免密。
3. 部署耗时约 3 分钟（前端 build + docker 重建 + 容器重启），耐心等脚本退出码 0。
4. 部署后验证清单：
   - [ ] `docker ps | grep llama-studio` → Up (healthy)
   - [ ] `curl -s -o /dev/null -w "%{http_code}" http://192.168.3.246:9100/health` → 200
   - [ ] DB 新列存在（`docker exec llama-studio python3 -c "sqlite3...PRAGMA table_info(...)"`）
   - [ ] 关键代码在容器内（grep 后端 py / 前端 bundle）
5. 遇到 422/500：先看 `docker logs llama-studio --since 10m | grep -E '422|500|ERROR'`，注意区分来源 IP（用户机器 vs 测试机器）。

---

## 4. 常用速查

```bash
# 本地
cd ~/projects/llama-sycl-studio
npm run build  # 前端构建验证（frontend/ 下）

# 部署
bash scripts/deploy.sh nuc12 --rebuild

# NUC12 容器内
ssh nuc12 "docker exec llama-studio ls -la /app/studio/frontend/dist/assets/"
ssh nuc12 "docker exec llama-studio grep -c '关键代码' /app/studio/backend/app/routers/presets.py"
ssh nuc12 "docker logs llama-studio --since 10m | grep -E '422|500'"

# API 测试（登录拿 token）
# POST /api/auth/login {"password":"qa1234"} → token
# GET/PUT /api/presets  Bearer token
```

## 5. 参考
- 架构/部署/参数详情：`docs/PROJECT-STATE.md`
- 构建细节：`docs/BUILD.md`
- 路线图：`docs/ROADMAP.md`
