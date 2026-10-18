# 旅行RPG · 城市拾遗

> 2026 飞猪 AI 旅行创新大赛参赛作品 · 基于 **阿里云百炼 Managed Agent** + **飞猪 flyai** 的 LBS 生成式城市剧本杀。

把用户脚下的真实城市变成游戏棋盘：选一条精选经典线路，智能体用**飞猪真实景点**做素材、由百炼大模型**实时生成有主线、有分叉的互动剧情**，每走一步都在推进一段只属于你、此时此刻此地的冒险。

## ✨ 核心特性

- **真实地点驱动叙事**：每个剧情节点绑定飞猪检索到的**真实 POI**（含坐标/地址/看点），而非虚构场景。
- **防幻觉"素材包"两阶段架构**：后端先检索并结构化真实 POI 成"素材包"，塞进 prompt 强制模型**只能从素材包里选点、以 poi_id 引用并只输出 JSON**；后端再 `extract_json + 回校`，引用了素材包外的地点直接判为幻觉拦截。**从机制上杜绝"编出一个不存在的地点"**。
- **精选经典线路（离线库 + 真实配图）**：内置 4 条城市级线路，全部真实景点 + 本地配图（飞猪主图，阿里 CDN resize 优化），命中离线库时**开局约 0.18s**、零外部检索依赖。
- **时长与结局控制**：开局可选「精短 / 标准 / 完整」局长，到达指定幕数自动收束为**大结局节点**；也可随时"结束本局"（带防误点的两段式确认）。
- **会话说断自恢复**：百炼托管会话空闲被回收时，`/api/chat` 检测 404 → 自动重建会话并把剧情进度无缝续接；前端 `localStorage` 存档，刷新可"继续上局"。
- **交互质感**：暗色电影感 H5，配图卡片、入场动效、逐个错峰选项、导航跳转与 GPS 到达打卡。

## 🧭 架构

```
H5 前端 (public/index.html, 原生 JS 单页)
        │  只与本地代理通信，密钥永不下发浏览器
        ▼
后端代理 (server_py.py, Python 标准库单文件, :8788)
        │  ├─ 离线线路库 offline/routes.json（真实 POI + 配图）
        │  ├─ flyai CLI 检索 → haversine 距离过滤 → 结构化"素材包"
        │  ├─ 百炼 Managed Agent (AgentStudio API) 生成剧情（SSE 流式回传）
        │  └─ 输出回校（防幻觉）+ poi_id 补全（坐标/导航/图片）
        ▼
密钥隔离：BAILIAN_API_KEY / FLYAI_API_KEY 仅存服务器 .env
```

## 🚀 快速开始（本地复现）

```bash
# 1) 依赖：Node 18+ 与飞猪 CLI（后端 subprocess 调用它检索真实 POI）
npm i -g @fly-ai/flyai-cli

# 2) 配置密钥
cp .env.example .env
#   填入 BAILIAN_API_KEY、FLYAI_API_KEY（及可选 WORKSPACE_ID/AGENT_ID/ENV_ID/ACCESS_KEY）

# 3) 启动（纯 Python 标准库，无需 pip 安装）
python3 server_py.py        # 默认监听 0.0.0.0:8788

# 4) 打开 http://127.0.0.1:8788  → 选线路 → 开局 → 选择推进
```

## 🅱️ 百炼使用说明

- **角色**：百炼位于作品**运行时链路**——每条局面对应一个百炼托管 Agent 会话，负责"基于真实素材包生成互动剧情"。
- **能力 / 模型**：阿里云百炼 **Managed Agent（托管智能体）**，模型为百炼默认配置（可在控制台切换 qwen 系列）。
- **调用方式**：后端通过 **百炼 AgentStudio HTTP API**（`POST /sessions` 建会话、`POST /sessions/{id}/events` 投递开局/推进/结局 prompt、`GET /sessions/{id}/events/stream` SSE 订阅流式回传）。
- **输入 → 输出**：输入 = 玩家位置/所选线路经后端真实检索得到的**结构化素材包 + 硬约束 prompt** → 百炼 Agent 产出 → 后端解析校验 → 输出 = 剧情节点 JSON（`node_title/story/bound_poi_id/choices[]/checkin`），前端渲染为配图卡片。
- **Skill**：`alibaba-flyai/flyai-skill` / `@fly-ai/flyai-cli`（飞猪真实 POI 检索）。

## 📦 离线线路库

- `offline/routes.json`：4 条精选线路（西湖十景 / 灵隐禅踪 / 南宋御街 / 北京中轴线），每个景点含真实坐标、地址、看点 `hook`、配图本地路径 `img`、来源 `pic_url`。
- 配图（`offline/img/*.webp`，来自飞猪主图 CDN）因体积未随仓库分发，可用工具重采/重下：
  ```bash
  python3 tools/harvest_offline.py 杭州 北京      # 城市级真实 POI 库
  python3 tools/curate_routes.py                  # 精选线路 + 下载配图到 offline/img
  ```

## 🗂 项目结构

```
server_py.py            # 后端：密钥保险箱 + 素材包检索/回校 + 百炼代理 + /api + /img
public/index.html       # 前端：暗色电影感单页（线路选择/剧情节点/结局/时长控制）
offline/routes.json     # 精选线路（真实 POI + 配图引用）
offline/img/            # 本地配图（.gitignore 排除，可 tools 重下）
tools/                  # 离线库采集/策展脚本（可重跑刷新）
.env.example / .gitignore
```

## 🔒 安全

- `.env` 已在 `.gitignore` 中排除；仓库仅含 `.env.example` 占位，**不提交任何 API Key / 密码等敏感信息**。
- 两个密钥只在服务端使用，H5 前端只与本地代理通信，密钥不下发浏览器。

## 🌐 在线 Demo

- 公网体验：**http://59.110.37.229:8788** （输入任意旅行名字进入 → 选线路即玩，无需定位）。

---

*2026 飞猪 AI 旅行创新大赛 · 「旅行RPG · 城市拾遗」*
