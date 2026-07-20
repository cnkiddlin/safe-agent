# Safe Agent - AI 订单助手

基于 Flask + WebSocket 的 AI 订单管理助手，集成以下企业级安全能力：

- **IBM Verify OIDC SSO 认证** — 对接真实 IBM Security Verify，支持 PKCE 授权码流程
- **HashiCorp Vault 密钥管理** — 在 Vault 中存储 API Token、Client Credential、应用秘钥
- **OBO Token → Vault Token 兑换机制** — 两层 Token 缓存（内存 + Session），模拟企业级安全凭证交换
- **OpenCode AI (DeepSeek)** 大语言模型驱动的智能对话交互

## 功能概述

- **AI 对话** — 通过 OpenCode AI (DeepSeek) 模型进行订单查询、修改与删除等自然语言交互
- **IBM Verify SSO 登录** — 真实 OIDC 授权码 + PKCE 流程，重定向到 IBM Verify 完成身份认证
- **OBO Token → Vault Token 兑换** — 模拟 Verify Client Credential → OBO Token → Vault Token → 应用秘钥的完整安全链
- **多级 Token 缓存** — 内存缓存 + Flask Session 持久化，防止 dev reloader 重启导致 Token 丢失
- **Vault 密钥管理** — 模拟 Vault 存储和获取 API Token、Client Credential、应用秘钥
- **订单管理 API** — 独立的 MCP 订单服务，提供 RESTful 订单 CRUD 接口
- **实时通信** — 基于 Flask-SocketIO 实现 WebSocket 双向消息推送
- **敏感操作确认** — 删除订单等高风险操作需要用户明确授权（IBM Verify 风格弹窗确认）

## 项目结构

```
safe-agent/
├── app.py                 # 主应用 — Flask Web 服务 + AI 对话路由
├── mcp_order_server.py    # MCP 订单管理服务
├── requirements.txt       # Python 依赖清单
├── templates/             # Jinja2 模板
│   ├── index.html         # 订单助手主页面
│   └── verify3.html       # 敏感操作授权确认面板（可嵌入 iframe）
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

### SSO 登录流程

点击首页的"登录"按钮，将自动跳转到 IBM Verify（order-platform-demo.verify.ibm.com）完成 SSO 身份认证，认证成功后返回主界面。流程基于 OIDC Authorization Code + PKCE 协议。

### 主要环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `OPENCODE_API_KEY` | OpenCode AI (DeepSeek) API 密钥 | 是 |
| `VERIFY_ISSUER` | IBM Verify 签发地址 | 否（有默认值） |
| `VERIFY_CLIENT_ID` | OIDC 应用客户端 ID | 是 |
| `VERIFY_CLIENT_SECRET` | OIDC 应用客户端密钥 | 是 |
| `VAULT_ADDR` | Vault 服务地址 | 否 |
| `VAULT_TOKEN` | Vault 认证令牌 | 否 |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 订单助手主页面 |
| `GET` | `/api/login` | 重定向到 IBM Verify SSO 登录 |
| `GET` | `/callback` | OIDC 回调端点，处理授权码交换 |
| `POST` | `/api/logout` | 登出（清除本地 Session） |
| `GET` | `/api/user` | 获取当前登录用户信息 |
| `GET` | `/api/check-auth` | 检查登录状态 |
| `GET` | `/api/mcp/tools` | 获取已注册 MCP 工具列表 |
| `POST` | `/api/mcp/tools` | 注册新的 MCP 工具 |
| `DELETE` | `/api/mcp/tools/<name>` | 删除指定 MCP 工具 |
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

当前版本采取混合模式运行：

- **IBM Verify OIDC 认证**：真实对接 order-platform-demo.verify.ibm.com，使用 PKCE 授权码流程完成 SSO 登录
- **Vault 令牌管理**：使用 Demo 模拟模式，Token 和凭证通过 `uuid` 和 `time.sleep` 模拟生成，仅展示安全集成流程，无需真实 Vault 实例
- OBO Token 兑换、Vault Token 缓存等在 Demo 模式下运行，后续可替换为真实服务调用

## 技术栈

- **后端** — Python / Flask / Flask-SocketIO
- **AI** — OpenCode AI (DeepSeek) API
- **认证** — IBM Verify OIDC（真实 SSO + PKCE）
- **密钥管理** — HashiCorp Vault（模拟）
- **前端** — Jinja2 / 原生 JavaScript + CSS（支持亮色/暗色主题）
- **HTTP 客户端** — httpx（异步 API 调用）

## 许可证

MIT
