# Safe Agent — AI 订单助手

基于 **Flask + WebSocket + DeepSeek** 的智能订单管理助手，集成企业级安全认证与令牌兑换链路：

- **IBM Verify OIDC SSO 认证** — 真实对接 IBM Security Verify，PKCE 授权码流程
- **IBM Verify Token Exchange** — 真实 OBO（On-Behalf-Of）令牌兑换，打通 Verify → Vault 安全链
- **HashiCorp Vault 密钥管理** — Agent 通过 AppRole 登录 Vault 读取凭证，OBO Token 登录 Vault 获取应用 API Token
- **多级 Token 缓存** — 内存 + Session 双层缓存，避免重复兑换，支持 TTL 自动过期
- **OpenCode AI (DeepSeek)** 大语言模型驱动的自然语言交互

## 项目结构

```
safe-agent/
├── app.py                    # 主应用 — Flask Web 服务 + Token Exchange + AI 对话
├── mcp_order_server.py       # MCP 订单管理服务（订单 CRUD）
├── requirements.txt          # Python 依赖
├── templates/
│   ├── index.html            # 订单助手主页面（聊天 + 侧面板）
│   └── verify3.html          # 敏感操作授权确认面板（iframe 嵌入）
├── static/
│   ├── app.js                # 前端交互（聊天、面板拖拽、授权弹窗、日志）
│   ├── style.css             # 样式文件
│   └── favicon.svg           # 网站图标
└── vault/
    ├── admin.hcl             # Vault Admin 策略配置
    ├── agent.hcl             # Vault Agent AppRole 策略（secret/verify 读权限）
    ├── orders-read.hcl       # Vault 订单读取策略（secret/order/* 读权限）
    └── keys.txt              # （敏感文件，已 .gitignore，不提交）
```

## 安全凭证交换流程

完整的 Token Exchange 链路，每次用户对话自动执行（缓存命中时跳过）：

```
用户 SSO 登录
    │
    ▼
access_token (sub_token)  ← 本地缓存
    │
    ▼
Agent Vault AppRole 登录
    ├─ role_id + secret_id → login → agent_vault_token  ← 缓存 TTL 300s
    │
    ▼
从 Vault secret/verify 读取凭证
    ├─ agent_client_id / agent_client_secret
    └─ sts_client_id / sts_client_secret
    │
    ▼
Agent Verify Client Credentials 登录
    ├─ agent_client_id + agent_client_secret → login → act_token  ← 缓存 TTL 300s
    │
    ▼
Token Exchange (OBO)
    ├─ subject_token = sub_token (用户)
    ├─ actor_token   = act_token (Agent)
    └─ → obo_token  ← 缓存 TTL 300s（内存 + Session）
    │
    ▼
OBO → Vault JWT 登录
    ├─ jwt = obo_token, role = "verify"
    └─ → vault_token  ← 缓存 TTL 300s（内存 + Session）
    │
    ▼
从 Vault secret/order/{query,delete} 读取 API Token
    └─ → api_token → 执行业务操作
```

### 缓存策略

| 缓存项 | 存储 | TTL | 命中时后端日志 |
|--------|------|-----|---------------|
| `agent_vault_token` | 内存 | 300s | `[TOKEN_CACHE] Agent Vault Token 缓存命中` |
| `agent_verify_token` (act_token) | 内存 | 300s | `[TOKEN_CACHE] Agent Verify Token 缓存命中` |
| `obo_token` | 内存 + Session | 300s | `[TOKEN_CACHE] OBO Token 缓存命中` |
| `obo_token` (Session 恢复) | Session → 内存 | 300s | `[TOKEN_CACHE] OBO Token Session 缓存命中` |
| `vault_token` | 内存 + Session | 300s | `[TOKEN_CACHE] Vault Token 缓存命中` |
| `vault_token` (Session 恢复) | Session → 内存 | 300s | `[TOKEN_CACHE] Vault Token Session 缓存命中` |

> 所有缓存项在过期后自动从上游重新兑换，Session 缓存用于在 dev reloader 重启后恢复令牌。

## 功能

- **AI 对话** — 通过 DeepSeek 模型自然语言查询、管理订单
- **IBM Verify SSO 登录** — 真实 OIDC 授权码 + PKCE 流程
- **真实 Token Exchange** — GET / POST 到 Verify token 端点兑换 OBO Token
- **Agent 日志面板** — 右侧面板实时展示每一步 Token Exchange 的详细日志，支持展开/折叠和滚动查看
- **Vault 密钥管理** — 真实 Vault AppRole 登录 + OBO Token JWT 认证
- **敏感操作确认** — 删除订单等高危操作通过 IBM Verify 风格弹窗授权
- **订单管理 API** — 独立的 MCP 订单服务（CRUD）
- **多级缓存** — 内存 + Session 双层缓存，避免重复的 Token Exchange 和 Vault 登录

## 快速开始

### 前置条件

- Python 3.10+
- HashiCorp Vault（本地运行，端口 8200）
- IBM Verify 应用（用于 SSO 登录和 Token Exchange）

### 安装

```bash
cd safe-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 环境变量

| 变量 | 说明 | 示例 / 默认值 | 必填 |
|------|------|---------------|------|
| `OPENCODE_API_KEY` | OpenCode AI (DeepSeek) API 密钥 | `sk-...` | 是 |
| `VERIFY_ISSUER` | IBM Verify 签发地址 | `https://order-platform-demo.verify.ibm.com` | 否 |
| `VERIFY_CLIENT_ID` | OIDC 应用客户端 ID（SSO 登录用） | `4b51b9d8-...` | 是 |
| `VERIFY_CLIENT_SECRET` | OIDC 应用客户端密钥 | `wSjMSRSg...` | 是 |
| `VAULT_ADDR` | Vault 服务地址 | `http://127.0.0.1:8200` | 否 |
| `agent_role_id` (或 `VAULT_ROLE_ID`) | Vault AppRole Role ID | `641b3885-...` | 是 |
| `agent_secret_id` (或 `VAULT_SECRET_ID`) | Vault AppRole Secret ID | `81d9fd9c-...` | 是 |

### 启动 Vault

```bash
# 开发模式启动
vault server -config=vault/admin.hcl

# 初始化（仅首次）
vault operator init -key-shares=5 -key-threshold=3

# 解封
vault operator unseal ...

# 登录
vault login ...

# 启用 AppRole
vault auth enable approle

# 创建策略
vault policy write agent vault/agent.hcl
vault policy write orders-read vault/orders-read.hcl

# 创建 AppRole
vault write auth/approle/role/agent \
    token_policies="agent,orders-read" \
    token_ttl=1h

# 获取认证信息
vault read auth/approle/role/agent/role-id
vault write -f auth/approle/role/agent/secret-id

# 写入 Verify 客户端凭证
vault kv put secret/verify \
    agent_client_id="<verify-agent-client-id>" \
    agent_client_secret="<verify-agent-client-secret>" \
    sts_client_id="<verify-sts-client-id>" \
    sts_client_secret="<verify-sts-client-secret>"

# 写入订单 API Token
vault kv put secret/order/query api_token="QUERY_API_TOKEN_DEMO_123456"
vault kv put secret/order/delete api_token="DELETE_API_TOKEN_DEMO_654321"
```

### 启动应用

```bash
# 设置环境变量（示例）
export OPENCODE_API_KEY="sk-..."
export VERIFY_CLIENT_ID="4b51b9d8-..."
export VERIFY_CLIENT_SECRET="wSjMSRSg..."
export agent_role_id="641b3885-..."
export agent_secret_id="81d9fd9c-..."

# 启动主应用 → http://127.0.0.1:18923
python app.py

# （可选）启动 MCP 订单服务 → http://127.0.0.1:18724
python mcp_order_server.py
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页面 |
| `GET` | `/api/login` | 重定向到 IBM Verify SSO 登录 |
| `GET` | `/callback` | OIDC 回调，交换授权码为 Token |
| `POST` | `/api/logout` | 登出（清除 Session） |
| `GET` | `/api/user` | 获取当前用户信息 |
| `GET` | `/api/check-auth` | 检查登录状态 |
| `GET` | `/api/mcp/tools` | 获取已注册 MCP 工具列表 |
| `POST` | `/api/mcp/tools` | 注册新的 MCP 工具 |
| `DELETE` | `/api/mcp/tools/<name>` | 删除指定 MCP 工具 |
| `GET` | `/api/agent/logs` | 获取 Agent 日志列表 |
| `DELETE` | `/api/agent/logs` | 清空 Agent 日志 |

### WebSocket 事件

| 事件 | 方向 | 说明 |
|------|------|------|
| `chat_message` | 客户端 → 服务端 | 发送用户消息 |
| `chat_response` | 服务端 → 客户端 | AI 回复（含 tool_calls） |
| `agent_log` | 服务端 → 客户端 | 实时 Agent 日志推送 |
| `request_verify` | 服务端 → 客户端 | 请求敏感操作授权 |
| `verify_response` | 客户端 → 服务端 | 用户授权确认/拒绝 |

## 技术栈

- **后端** — Python / Flask / Flask-SocketIO
- **AI 模型** — OpenCode AI (DeepSeek) API
- **认证** — IBM Verify OIDC（SSO）+ Token Exchange（OBO）
- **密钥管理** — HashiCorp Vault（AppRole + JWT Auth）
- **前端** — Jinja2 / 原生 JavaScript + CSS
- **HTTP 客户端** — httpx（同步 + 异步）

## 许可证

MIT
