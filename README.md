# Safe Agent - AI 订单助手

基于 Flask + WebSocket 的 AI 订单管理助手，通过 Demo 模拟模式展示以下集成流程：

- **IBM Verify OIDC 认证** — 模拟多步骤 OAuth 授权流程
- **HashiCorp Vault 密钥管理** — 在 Vault 中存储 API Token、Client Credential、应用秘钥
- **OBO Token → Vault Token 兑换机制** — 两层 Token 缓存（内存 + Session），模拟企业级安全凭证交换
- **OpenCode AI (DeepSeek)** 大语言模型驱动的智能对话交互

## 功能概述

- **AI 对话** — 通过 OpenCode AI (DeepSeek) 模型进行订单查询、修改与删除等自然语言交互
- **OBO Token → Vault Token 兑换** — 模拟 Verify Client Credential → OBO Token → Vault Token → 应用秘钥的完整安全链
- **多级 Token 缓存** — 内存缓存 + Flask Session 持久化，防止 dev reloader 重启导致 Token 丢失
- **IBM Verify 授权模拟** — 三步骤 OIDC 登录流程（登录 → 授权确认 → 授权完成）
- **Vault 密钥管理** — 模拟 Vault 存储和获取 API Token、Client Credential、应用秘钥
- **订单管理 API** — 独立的 MCP 订单服务，提供 RESTful 订单 CRUD 接口
- **实时通信** — 基于 Flask-SocketIO 实现 WebSocket 双向消息推送
- **敏感操作确认** — 删除订单等高风险操作需要用户明确授权（前端弹窗确认）

## 项目结构

```
safe-agent/
├── app.py                 # 主应用 — Flask Web 服务 + AI 对话路由
├── mcp_order_server.py    # MCP 订单管理服务
├── requirements.txt       # Python 依赖清单
├── templates/             # Jinja2 模板
│   ├── index.html         # 订单助手主页面
│   ├── verify1.html       # IBM Verify 登录页
│   ├── verify2.html       # 授权确认页
│   └── verify3.html       # 授权完成页
├── static/                # 静态资源
│   ├── app.js             # 前端交互逻辑（聊天、授权、确认弹窗）
│   ├── style.css          # 样式文件（亮色/暗色主题）
│   └── favicon.svg        # 网站图标（订单盒图标）
├── vault/                 # Vault 配置
│   ├── vault.hcl          # Vault 服务配置
│   └── keys.txt           # （本地敏感文件，不提交）
└── venv/                  # Python 虚拟环境（本地）
```

## 快速开始

### 前置条件

- Python 3.10+
- HashiCorp Vault（可选，用于密钥管理）

### 安装与运行

```bash
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 设置环境变量
export OPENCODE_API_KEY="your-api-key"

# 4. 启动主应用（默认端口 18923）
python app.py          # → http://127.0.0.1:18923

# 5. （可选）启动 MCP 订单服务（默认端口 5001）
python mcp_order_server.py
```

> **注意**：应用默认运行在 `127.0.0.1:18923`，如果端口被占用可修改 `app.py` 中的 `port` 参数。

### 主要环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `OPENCODE_API_KEY` | OpenCode AI (DeepSeek) API 密钥 | 是 |
| `VAULT_ADDR` | Vault 服务地址 | 否 |
| `VAULT_TOKEN` | Vault 认证令牌 | 否 |

其余配置（Vault Credential 等）在当前 Demo 模式下使用内建模拟数据，无需额外配置。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 订单助手主页面 |
| `POST` | `/chat` | AI 对话接口 |
| `POST` | `/set-org` | 设置当前组织机构（影响订单查询范围） |
| `POST` | `/authorize` | 模拟用户授权确认（重置认证流程） |
| `GET` | `/api/orders` | MCP — 获取订单列表 |
| `DELETE` | `/api/orders/<id>` | MCP — 删除指定订单 |

## Token 兑换流程

```
安全凭证链：
  Verify Client Credential
       ↓ (Client → Verify)
  OBO Token               ← 内存缓存 + Session 缓存（TTL 5 分钟）
       ↓ (OBO → Vault)
  Vault Token              ← 内存缓存 + Session 缓存（TTL 5 分钟）
       ↓ (Vault → App Secret Key)
  应用秘钥 / API Token
```

- 两层缓存策略：先检查进程内字典缓存，再检查 Session 缓存（跨请求、防 reloader 丢失）
- 每个 Token 层独立 TTL，过期后自动从上游重新兑换
- 敏感操作（如删除订单）需要用户弹窗确认后，才从 Vault 获取对应操作的 API Token

## Demo 模拟说明

当前版本使用 Demo 模拟模式运行：

- **无外部依赖要求**：不需要真实 IBM Verify、HashiCorp Vault 实例
- 凭证生成使用 `uuid` 和 `time.sleep` 模拟网络交互延迟
- 所有 Token 和凭证均为本地生成，仅用于展示安全集成流程
- 如需对接真实服务，可以替换 `get_verify_client_credential_from_vault()` 和 `exchange_to_obo_token()` 等模拟函数

## 技术栈

- **后端** — Python / Flask / Flask-SocketIO
- **AI** — OpenCode AI (DeepSeek) API
- **认证** — IBM Verify OIDC（模拟）
- **密钥管理** — HashiCorp Vault（模拟）
- **前端** — Jinja2 / 原生 JavaScript + CSS（支持亮色/暗色主题）
- **HTTP 客户端** — httpx（异步 API 调用）

## 许可证

MIT
