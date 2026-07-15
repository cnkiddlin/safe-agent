# Safe Agent - AI 订单助手

基于 Flask + WebSocket 的 AI 订单管理助手，集成 IBM Verify OIDC 认证、HashiCorp Vault 密钥管理，
以及 OpenCode AI (DeepSeek) 大语言模型驱动的智能对话交互。

## 功能概述

- **AI 对话** — 通过 OpenCode AI (DeepSeek) 模型进行自然语言订单查询与管理
- **OIDC 认证** — 集成 IBM Verify 身份认证（模拟多步骤 OAuth 流程）
- **Vault 密钥管理** — 使用 HashiCorp Vault 存储和管理敏感凭证
- **订单管理 API** — 独立的 MCP 订单服务，提供 RESTful 订单 CRUD 接口
- **实时通信** — 基于 Flask-SocketIO 实现 WebSocket 双向消息推送

## 项目结构

```
safe-agent/
├── app.py                 # 主应用 — Flask Web 服务 + AI 对话路由
├── mcp_order_server.py    # MCP 订单管理服务
├── templates/             # Jinja2 模板
│   ├── index.html         # 订单助手主页面
│   ├── verify1.html       # IBM Verify 登录页
│   ├── verify2.html       # 授权确认页
│   └── verify3.html       # 授权完成页
├── static/                # 静态资源
│   ├── app.js             # 前端交互逻辑
│   ├── style.css          # 样式文件
│   └── favicon.svg        # 网站图标
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
# 1. 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 设置环境变量
export OPENCODE_API_KEY="your-api-key"

# 4. 启动主应用（默认端口 5000）
python app.py

# 5. （可选）启动 MCP 订单服务（默认端口 5001）
python mcp_order_server.py
```

### 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `OPENCODE_API_KEY` | OpenCode AI API 密钥 | 是 |
| `VAULT_ADDR` | Vault 服务地址 | 否 |
| `VAULT_TOKEN` | Vault 认证令牌 | 否 |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 订单助手主页面 |
| `POST` | `/chat` | AI 对话接口 |
| `POST` | `/set-org` | 设置当前组织机构 |
| `GET` | `/api/orders` | MCP — 获取订单列表 |
| `DELETE` | `/api/orders/<id>` | MCP — 删除指定订单 |

## 技术栈

- **后端** — Python / Flask / Flask-SocketIO
- **AI** — OpenCode AI (DeepSeek) API
- **认证** — IBM Verify (OIDC)
- **密钥管理** — HashiCorp Vault
- **前端** — Jinja2 / 原生 JavaScript + CSS

## 许可证

MIT
