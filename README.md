# Enrui AI Commerce Agent

## 项目文档

- [技术架构说明](docs/01-TECHNICAL-ARCHITECTURE.md)：技术栈、系统分层、数据域、部署与安全边界。
- [功能说明](docs/02-FEATURES.md)：当前功能范围和推荐业务流程。
- [功能实现与开发指南](docs/03-IMPLEMENTATION-GUIDE.md)：关键入口、实现链路、扩展约定与技术债。
- [业务流程图索引](docs/04-FLOWCHARTS.md)：商品详情页图片生成的完整流程图、分页说明与维护约定。

## 阿里云一键部署

完整服务器交接和验收清单见 [`deploy/DEPLOY_HANDOFF.md`](deploy/DEPLOY_HANDOFF.md)。

服务器要求：Ubuntu 22.04+、root 权限，并由宿主机反向代理监听公网 80/443。应用前端默认仅监听 `127.0.0.1:8080`，宿主机反向代理应将域名流量转发到该地址。首次部署在服务器执行：

```bash
git clone https://github.com/Dreamsmama/enrui-ai-commerce-agent.git /opt/enrui-ai-commerce-agent/repository
bash /opt/enrui-ai-commerce-agent/repository/deploy.sh
```

首次运行会创建 `/opt/enrui-ai-commerce-agent/shared/.env` 并停止。填写其中全部 `FILL_` 配置后再次执行同一条 `deploy.sh` 命令即可完成幂等检查、构建、启动和健康检查。部署日志保存在 `/opt/enrui-ai-commerce-agent/shared/logs/`；新版本健康检查失败时会自动回退到上一次运行中的镜像。

以后代码 push 到 `main` 后，在服务器只需执行：

```bash
bash /opt/enrui-ai-commerce-agent/repository/deploy.sh
```

生产配置独立保存在服务器 `shared/.env`，不会被 `git pull` 覆盖，也不会提交到仓库。
可通过 `FRONTEND_BIND`、`FRONTEND_PORT` 和 `HEALTHCHECK_URL` 覆盖默认监听地址、端口与部署健康检查地址。

## 商品知识库与详情页 Demo 闭环

当前版本已支持：

1. 创建商品并录入品牌、成分、使用方法、规格等结构化资料。
2. 在知识库中添加商品级或全局品牌级文本/文档，自动分块并优先使用向量检索，未配置 Embedding 时回退关键词检索。
3. 在商品页上传产品图片、品牌素材、证书、检测报告和设计方法论 / Skill 文件。
4. Agent 生成时统一读取商品结构化资料、商品/品牌知识片段和素材说明，生成详情页模块。
5. 在详情页编辑器中单独重新生成模块、编辑模块 Markdown、调整模块顺序并保存组合结果。
6. 未配置模型账号时使用确定性 Mock LLM/Embedding 跑通完整流程；配置真实账号后关闭 Mock 模式。
7. 知识文档支持 PDF、DOCX、TXT、Markdown、CSV、JSON，并限制上传文件大小。
8. 生成任务记录尝试次数，失败任务可在最大次数内重试。
9. 详情页自动执行缺失模块、商品事实覆盖和高风险夸大表述检查，并展示质量分。
10. 支持下载 Markdown，以及通过浏览器打印生成 PDF。

知识库实现沿用 `enrui-ai-platform` 的最小核心路径：解析/入库 → 分块 → Embedding → 检索 → 上下文组装；Demo 暂不迁移其完整多租户、Repository、混合检索和 Rerank 架构。

### 模型模式

本地开发默认：

```env
LLM_MOCK_MODE=true
```

接入真实模型时在 `backend/.env` 设置 API 地址、Key、模型名，并修改：

```env
LLM_MOCK_MODE=false
```

火山方舟 Seed 模型建议同时配置：

```env
LLM_DISABLE_THINKING=true
LLM_TIMEOUT_SECONDS=90
```

详情页工作流包含多个串行 Agent，关闭深度思考可以显著降低生成等待时间；需要复杂推理时可按环境重新开启。

不要将 `backend/.env` 或真实 API Key 提交到代码仓库。

### 当前商业化边界

当前代码可离线演示和进行业务验收。正式对外上线前仍需接入实际租户/认证体系、对象存储、异步任务队列、监控告警、内容合规规则和最终长图/PDF 导出服务。

企业级 **AI 商品详情页生成助手** — 面向电商商家的多模态营销内容生成平台。

用于 AI Application Engineer / AI Solution Engineer / FDE 岗位作品展示。

## 能力亮点

| 模块 | 说明 |
|------|------|
| **多模态商品分析** | 文字 + 图片（Vision LLM）理解商品类型、特点、优势 |
| **Agent Workflow** | 商品理解 → 消费者分析 → 营销策略 → 详情页生成，流水线编排 |
| **AI 详情页编辑器** | Markdown 预览；按模块重生成 / 优化语气 / 切换目标用户 |
| **RAG 知识库** | 说明书/品牌资料切片 → Embedding → 向量检索，生成时引用 |
| **历史管理** | 商品项目、生成记录、修改记录持久化（SQLite） |
| **SaaS 后台** | Dashboard / 创建商品 / 生成编辑 / 知识库 / 历史 |

## 技术栈

- **Frontend**: React + TypeScript · Vite · TailwindCSS · Axios · React Router
- **Backend**: Python FastAPI · SQLAlchemy · SQLite · OpenAI Compatible API

## 项目结构

```
enrui-ai-commerce-agent/
├── backend/
│   ├── app/
│   │   ├── agents/          # 多 Agent Workflow + 编辑器
│   │   ├── api/             # REST 路由
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── rag/             # 切片 / Embedding / 检索
│   │   ├── schemas/         # Pydantic schemas
│   │   ├── services/        # LLM 客户端
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── uploads/
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   └── types/
│   └── package.json
├── .env.example
└── README.md
```

## 快速开始

### 1. 配置 LLM（OpenAI Compatible）

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`：

```env
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=gpt-4o
LLM_VISION_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small
```

兼容任意 OpenAI Compatible 接口（OpenAI / Azure / DeepSeek / 通义 / 本地 vLLM 等），只要提供 Chat Completions + Embeddings（多模态需 Vision 模型）。

### 2. 启动后端

```bash
# 方式一
./start-backend.sh

# 方式二
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> 请确保本机 **8000** 端口空闲。健康检查：http://127.0.0.1:8000/api/health  
> API 文档：http://127.0.0.1:8000/docs

### 3. 启动前端

```bash
# 方式一
./start-frontend.sh

# 方式二
cd frontend
npm install
npm run dev
```

打开：http://127.0.0.1:5173

前端已通过 Vite proxy 将 `/api`、`/uploads` 转发到后端 `:8000`。

### 4. 安装视频采集插件

启动 Docker 前端后, 在编导灵感页面提交抖音, 小红书, 快手或 TikTok 链接. 页面未检测到插件时会提供 `enrui-video-capture-extension.zip`下载入口.

解压后打开 `chrome://extensions`, 开启开发者模式, 选择"加载已解压的扩展程序", 然后选中解压目录. 本地 Docker 环境的应用地址为 `http://127.0.0.1:8080`, Vite 开发环境使用 `http://127.0.0.1:5173`.

插件支持在编导灵感页面粘贴链接自动采集, 也支持在原视频页面点击插件图标后选择"采集当前视频". 平台登录状态只保留在用户浏览器中, 不上传 Cookie.

## 使用流程

1. **知识库**（可选）：上传产品说明书 / 品牌资料，自动切片并 Embedding
2. **创建商品**：填写名称、类别、价格、描述、目标用户；上传或填写图片 URL
3. **启动生成**：后台运行 4 个 Agent，轮询任务状态
4. **编辑优化**：重新生成标题 / 优化卖点 / 优化语气 / 更适合年轻用户
5. **历史管理**：查看或删除商品与生成记录

## Agent Workflow

```
商品输入（文本 + 图片） + RAG 检索上下文
        │
        ▼
┌─────────────────────┐
│ Agent1 商品理解      │  类型 / 特点 / 优势 / 购买理由（Vision）
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Agent2 消费者分析    │  画像 / 场景 / 痛点 / 决策因素
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Agent3 营销策略      │  定位 / 卖点排序 / 竞争优势 / 主图文案
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Agent4 详情页生成    │  标题·卖点·优势·场景·痛点方案·FAQ·售后
└─────────────────────┘
```

## 主要 API

| Method | Path | 说明 |
|--------|------|------|
| GET/POST | `/api/products` | 商品列表 / 创建 |
| POST | `/api/products/{id}/generate` | 启动 Agent 生成 |
| GET | `/api/generations/{id}` | 查询生成结果 |
| POST | `/api/generations/{id}/edit` | 模块重生成 / 语气 / 受众 |
| GET/POST | `/api/knowledge` | 知识库文档 |
| POST | `/api/knowledge/upload` | 上传文本文件入库 |
| GET | `/api/dashboard/stats` | Dashboard 统计 |
| POST | `/api/director-projects/capture-sessions` | 创建浏览器视频采集会话 |
| POST | `/api/director-projects/capture-sessions/{id}/upload` | 上传插件采集的视频 |

## License

## 导入百雀羚 Demo 数据

仓库内的 `backend/demo_data/pechoin-demo` 包含商品资料、4 张商品素材、品牌知识和设计 Skill，不包含用户邮箱、密码哈希、生成历史或 API Key。

如需直接使用当前完整 SQLite 数据快照（包含本地账户、项目和生成记录），执行：

```bash
cp backend/demo_data/commerce_agent.db backend/commerce_agent.db
```

该快照仅用于团队内部开发，不能部署到公开生产环境。

同事先启动项目并注册自己的账户，然后执行：

```bash
cd backend
.venv/bin/python scripts/transfer_demo_data.py import \
  --email 同事的登录邮箱 \
  --input demo_data/pechoin-demo
```

需要重新导出某个账户所属租户的业务数据时执行：

```bash
cd backend
.venv/bin/python scripts/transfer_demo_data.py export \
  --email 登录邮箱 \
  --output demo_data/导出目录
```

导入按商品名称、知识标题和 Skill 名称幂等更新，不会复制账户凭据。

MIT — 用于学习与作品集展示。
