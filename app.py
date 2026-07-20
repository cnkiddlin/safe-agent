import json
import time
import os
import uuid
import threading
from datetime import datetime

import httpx
import hvac
from flask import Flask, render_template, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit
import secrets
import urllib.parse
import hashlib
import base64
import jwt
from jwt import PyJWKClient

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# Session Cookie 配置：确保跨域 OIDC 重定向能携带 cookie
# Session Cookie 配置：本地 HTTP 开发环境关闭 Secure，确保 session 在 OIDC 重定向后仍能被浏览器传回
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
)

socketio = SocketIO(app, cors_allowed_origins="*")

# ====== 配置 ======
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_MODEL = "deepseek-v4-flash"

# ====== IBM Verify OIDC SSO 配置 ======
VERIFY_ISSUER = os.getenv("VERIFY_ISSUER", "https://order-platform-demo.verify.ibm.com")
VERIFY_CLIENT_ID = os.environ["VERIFY_CLIENT_ID"]
VERIFY_CLIENT_SECRET = os.environ["VERIFY_CLIENT_SECRET"]
VERIFY_REDIRECT_URI = "http://localhost:18923/callback"
VERIFY_SCOPE = "openid profile email"

# OIDC endpoints (IBM Security Verify 标准路径)
VERIFY_AUTH_ENDPOINT = f"{VERIFY_ISSUER}/oauth2/authorize"
VERIFY_TOKEN_ENDPOINT = f"{VERIFY_ISSUER}/oauth2/token"
VERIFY_JWKS_ENDPOINT = f"{VERIFY_ISSUER}/oauth2/jwks"
VERIFY_END_SESSION_ENDPOINT = f"{VERIFY_ISSUER}/oauth2/end_session"

# JWKS 客户端缓存（懒加载）
_jwks_client = None


def get_jwks_client():
    """获取并缓存 JWKS 客户端"""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(VERIFY_JWKS_ENDPOINT, cache_keys=True)
    return _jwks_client


# ====== Agent 日志 ======
agent_logs = []


def add_log(level, message, detail=""):
    log_entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "level": level,
        "message": message,
        "detail": detail,
    }
    agent_logs.append(log_entry)
    socketio.emit("agent_log", log_entry)
    return log_entry


# ====== OIDC State Store（服务端保存，避免跨域 Cookie 丢失问题） ======
oidc_states = {}
OIDC_STATE_TTL = 600  # 10 分钟过期


def cleanup_oidc_states():
    """定期清理过期的 OIDC state"""
    while True:
        time.sleep(60)
        now = time.time()
        expired = [k for k, v in oidc_states.items()
                   if now - v.get("created_at", 0) > OIDC_STATE_TTL]
        for k in expired:
            oidc_states.pop(k, None)
        if expired:
            add_log("AGENT", f"清理 {len(expired)} 个过期的 OIDC state")


cleanup_thread = threading.Thread(target=cleanup_oidc_states, daemon=True)
cleanup_thread.start()


# ====== Agent Token Exchange 机制（OBO Token → Vault Token 兑换） ======
# 缓存 Vault Token，用于后续获取 API Token
vault_token_cache = {
    "token": None,
    "cached_at": 0.0,
    "expires_at": 0.0,
}
VAULT_TOKEN_CACHE_TTL = 300  # 5 分钟缓存

# OBO Token 缓存
obo_token_cache = {
    "token": None,
    "cached_at": 0.0,
    "expires_at": 0.0,
}
OBO_TOKEN_CACHE_TTL = 300  # 5 分钟缓存


def get_verify_client_credential_from_vault():
    """从 Vault 获取预先存放的 Verify Client Credential（Demo 模拟模式）"""
    time.sleep(0.5)
    credential = {
        "client_id": "fb3951ec-4755-4c63-9d6a-c68df2b98620",
        "client_secret": f"mock-secret-{uuid.uuid4().hex[:16]}",
    }
    add_log("AGENT", f"从 Vault 获取 Verify Client Credential",
            f"client_id: {credential['client_id']}")
    add_log("AGENT", "Verify Client Credential 获取成功")
    return credential


def exchange_to_obo_token(client_credential):
    """用 Verify Client Credential 向 Verify 发起 OBO Token Exchange（Demo 模拟模式）"""
    time.sleep(0.8)
    obo_token = f"obo-token-{uuid.uuid4().hex[:16]}"
    add_log("AGENT", f"执行 Token Exchange，获取 OBO Token",
            f"TTL {OBO_TOKEN_CACHE_TTL // 60} 分钟")
    return obo_token


def ensure_obo_token() -> str:
    """获取 OBO Token，优先使用本地缓存"""
    now = time.time()
    cached = obo_token_cache["token"]
    expires = obo_token_cache["expires_at"]

    if cached and expires > now:
        remaining = int(expires - now)
        add_log("AGENT", f"OBO Token 缓存命中，直接复用（剩余 {remaining}s）")
        return cached

    # 2. 检查 Session 缓存（跨请求持久化，防止 reloader 重启丢失）
    sess_obo_token = session.get("obo_token")
    sess_expires = session.get("obo_token_expires_at", 0.0)
    if sess_obo_token and sess_expires > now:
        obo_token_cache["token"] = sess_obo_token
        obo_token_cache["cached_at"] = session.get("obo_token_cached_at", now)
        obo_token_cache["expires_at"] = sess_expires
        remaining = int(sess_expires - now)
        add_log("AGENT", f"OBO Token Session 缓存命中，直接复用（剩余 {remaining}s）")
        return sess_obo_token

    credential = get_verify_client_credential_from_vault()
    obo_token = exchange_to_obo_token(credential)

    now = time.time()
    obo_token_cache["token"] = obo_token
    obo_token_cache["cached_at"] = now
    obo_token_cache["expires_at"] = now + OBO_TOKEN_CACHE_TTL

    # 同步写入 Session，即使进程重启也能复用
    session["obo_token"] = obo_token
    session["obo_token_cached_at"] = now
    session["obo_token_expires_at"] = now + OBO_TOKEN_CACHE_TTL

    return obo_token


def get_app_secret_key(vault_token: str = ""):
    """获取应用秘钥（Demo 模拟模式）"""
    time.sleep(0.3)
    app_secret = f"app-secret-{uuid.uuid4().hex[:16]}"
    return {
        "status": "SUCCESS",
        "app_secret_key": app_secret,
    }


def ensure_vault_token() -> str:
    """获取 Vault Token，优先使用本地缓存"""
    now = time.time()
    cached = vault_token_cache["token"]
    expires = vault_token_cache["expires_at"]

    if cached and expires > now:
        remaining = int(expires - now)
        add_log("AGENT", f"Vault Token 缓存命中，直接复用（剩余 {remaining}s）")
        return cached

    # 2. 检查 Session 缓存（跨请求持久化，防止 reloader 重启丢失）
    sess_vault_token = session.get("vault_token")
    sess_expires = session.get("vault_token_expires_at", 0.0)
    if sess_vault_token and sess_expires > now:
        vault_token_cache["token"] = sess_vault_token
        vault_token_cache["cached_at"] = session.get("vault_token_cached_at", now)
        vault_token_cache["expires_at"] = sess_expires
        remaining = int(sess_expires - now)
        add_log("AGENT", f"Vault Token Session 缓存命中，直接复用（剩余 {remaining}s）")
        return sess_vault_token

    add_log("AGENT", "未检测到 OBO Token 和 Vault Token 的缓存")

    # 确保 OBO Token 可用
    obo_token = ensure_obo_token()

    # 用 OBO Token 向 Vault 进行身份验证
    time.sleep(0.5)
    vault_token = f"vault-token-{uuid.uuid4().hex[:16]}"
    add_log("AGENT", "使用 OBO Token 通过 Vault 身份验证")
    add_log("AGENT", f"Vault Token 已缓存（TTL {VAULT_TOKEN_CACHE_TTL // 60} 分钟）")

    now = time.time()
    vault_token_cache["token"] = vault_token
    vault_token_cache["cached_at"] = now
    vault_token_cache["expires_at"] = now + VAULT_TOKEN_CACHE_TTL

    # 同步写入 Session，即使进程重启也能复用
    session["vault_token"] = vault_token
    session["vault_token_cached_at"] = now
    session["vault_token_expires_at"] = now + VAULT_TOKEN_CACHE_TTL

    # 获取应用秘钥
    get_app_secret_key(vault_token)

    return vault_token


# ====== MCP 工具注册 ======
mcp_tools = []
mcp_tool_definitions = []


def register_mcp_tool(name, description, parameters, handler):
    tool = {
        "name": name,
        "description": description,
        "parameters": parameters,
        "handler": handler,
    }
    mcp_tools.append(tool)
    definition = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
    mcp_tool_definitions.append(definition)
    return tool


# ====== 内置工具 ======
async def _handle_weather(args):
    city = args.get("city", "未知")
    add_log("AGENT", f"调用天气查询: {city}")
    return json.dumps({"city": city, "temperature": "26°C", "weather": "多云", "humidity": "60%"}, ensure_ascii=False)


async def _handle_calc(args):
    expr = args.get("expression", "")
    add_log("AGENT", f"调用计算器: {expr}")
    try:
        result = eval(expr, {"__builtins__": {}}, {})
        return json.dumps({"expression": expr, "result": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _handle_time(args):
    add_log("AGENT", "查询当前时间")
    now = datetime.now()
    return json.dumps({"current_time": now.strftime("%Y-%m-%d %H:%M:%S"), "timezone": "Asia/Shanghai"}, ensure_ascii=False)


register_mcp_tool("get_weather", "查询指定城市的天气信息",
                  {"type": "object", "properties": {"city": {"type": "string", "description": "城市名称"}}, "required": ["city"]},
                  _handle_weather)
register_mcp_tool("calculator", "执行数学计算",
                  {"type": "object", "properties": {"expression": {"type": "string", "description": "数学表达式"}}, "required": ["expression"]},
                  _handle_calc)
register_mcp_tool("get_current_time", "获取当前时间和时区信息",
                  {"type": "object", "properties": {}, "required": []},
                  _handle_time)

# ====== 订单工具（通过 HTTP 请求 MCP Server） ======
MCP_ORDER_SERVER_URL = "http://127.0.0.1:18724"


def get_api_token_from_vault(operation_name: str, vault_token: str = ""):
    """从 Vault 获取指定操作类型的 API Token——通过 Vault Token 认证（Demo 模式）"""
    vault_addr = 'http://127.0.0.1:8200'
    # Demo 模式：内部使用预先配置的 Vault Token 连接真实 Vault
    demo_vault_token = os.getenv("VAULT_TOKEN", "<your-vault-token>")

    if operation_name not in ("get", "delete"):
        return {"status": "FAILED", "message": "operation_name 必须是 'get' 或 'delete'"}

    add_log("AGENT", f"准备从 Vault 获取 API Token ( {operation_name.upper()} 操作)")

    client = hvac.Client(url=vault_addr, token=demo_vault_token)
    if not client.is_authenticated():
        add_log("ERROR", "Vault 身份验证失败")
        return {"status": "FAILED", "message": "Failed to authenticate with Vault"}

    try:
        response = client.secrets.kv.v1.read_secret(
            mount_point="secret",
            path=f"order/user_zhangsan/{operation_name}",
        )
        api_token = response["data"][f"{operation_name}_api_token"]
        add_log("AGENT", f"获取 API Token（ {operation_name.upper()} 操作）")
        return {
            "status": "SUCCESS",
            "operation": operation_name,
            "api_token": api_token,
        }
    except Exception as e:
        add_log("ERROR", f"读取 Vault Secret 失败: {e}")
        return {"status": "FAILED", "message": f"Failed to read secret: {str(e)}"}


async def _handle_get_api_token(args):
    """处理 get_api_token_from_vault 工具调用"""
    vault_token_op = ensure_vault_token()
    operation = args.get("operation_name", "")
    result = get_api_token_from_vault(operation, vault_token_op)
    return json.dumps(result, ensure_ascii=False)


async def _handle_list_orders(args):
    api_token = args.get("api_token", "")
    add_log("AGENT", "调用 list_orders 服务，查看订单列表")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            status = args.get("status", "")
            params = {"api_token": api_token}
            if status:
                params["status"] = status
            resp = await client.get(f"{MCP_ORDER_SERVER_URL}/api/orders", params=params)
            try:
                data = resp.json()
                if data.get("success"):
                    orders = data.get("orders", [])
                    lines = [f"共 {len(orders)} 条订单："]
                    for o in orders:
                        lines.append(f"- {o['id']} | {o['customer']} | {o['product']} | {o['amount']} | {o['status']}")
                    return "\n".join(lines)
            except Exception:
                pass
            return resp.text
    except Exception as e:
        add_log("ERROR", f"请求 MCP Server 失败: {e}")
        return json.dumps({"error": f"无法连接到订单服务: {str(e)}"}, ensure_ascii=False)


async def _handle_delete_order(args):
    order_id = args.get("order_id", "")
    api_token = args.get("api_token", "")
    add_log("AGENT", f"删除订单 {order_id}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            params = {"api_token": api_token}
            resp = await client.delete(f"{MCP_ORDER_SERVER_URL}/api/orders/{order_id}", params=params)
            return resp.text
    except Exception as e:
        add_log("ERROR", f"请求 MCP Server 失败: {e}")
        return json.dumps({"error": f"无法连接到订单服务: {str(e)}"}, ensure_ascii=False)


register_mcp_tool(
    "get_api_token_from_vault",
    "仅在查询订单列表时手动调用。从 Vault 获取 API Token 用于 list_orders 工具。"
    "operation_name 只传 'get'：查询订单列表时传 'get'。"
    "删除操作不需要调用此工具，系统会自动处理。",
    {
        "type": "object",
        "properties": {
            "operation_name": {
                "type": "string",
                "description": "固定传 'get'（仅用于查询订单列表）",
                "enum": ["get", "delete"]
            }
        },
        "required": ["operation_name"]
    },
    _handle_get_api_token
)

register_mcp_tool(
    "list_orders",
    "查看所有订单列表。必须先调用 get_api_token_from_vault(operation_name='get') 获取 API Token，再将获取到的 api_token 作为此工具的参数传入。"
    "可选的 status 参数用于按订单状态进行过滤。如果没有先获取 API Token，调用将失败。",
    {
        "type": "object",
        "properties": {
            "api_token": {"type": "string", "description": "【必填】通过 get_api_token_from_vault('get') 获取的 API Token，用于验证访问权限"},
            "status": {"type": "string", "description": "订单状态过滤（可选）：待付款/处理中/已发货/已完成"}
        },
        "required": ["api_token"]
    },
    _handle_list_orders
)

register_mcp_tool(
    "delete_order",
    "根据订单 ID 删除指定订单。直接调用即可，不要先调用 get_api_token_from_vault。"
    "系统会自动处理：1）弹出安全验证界面让用户授权 2）从 Vault 获取 API Token 3）执行删除。",
    {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "要删除的订单 ID，如 ORD-20260701-001"},
            "api_token": {"type": "string", "description": "可选。一般不需要传，系统会在用户授权后自动获取"}
        },
        "required": ["order_id"]
    },
    _handle_delete_order
)

# ====== 敏感操作验证机制 ======
pending_verifications = {}


# ====== 路由 ======

@app.route("/")
def index():
    """主页"""
    user = session.get("user")
    return render_template("index.html", logged_in=bool(user))




@app.route("/verify3")
def verify3():
    """操作验证页面（可嵌入 iframe）"""
    return render_template("verify3.html")


@app.route("/api/login", methods=["GET"])
def login():
    """重定向到 IBM Verify SSO 登录页面（PKCE 授权码流程）"""
    state = secrets.token_urlsafe(32)

    # 生成 PKCE code_verifier 和 code_challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_digest).rstrip(b"=").decode("ascii")

    # 服务端保存 state + code_verifier（不依赖 Session Cookie）
    oidc_states[state] = {
        "code_verifier": code_verifier,
        "created_at": time.time(),
    }

    params = {
        "response_type": "code",
        "client_id": VERIFY_CLIENT_ID,
        "redirect_uri": VERIFY_REDIRECT_URI,
        "scope": VERIFY_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",   # 始终显示 Verify 登录页面，不自动使用已有 session
    }
    auth_url = f"{VERIFY_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"
    # 同时保存到 session 作为备份（Secure=False 后 session cookie 能跨同站点重定向保持）
    session["oidc_state"] = state
    session["oidc_code_verifier"] = code_verifier

    add_log("AGENT", f"重定向到 IBM Verify SSO（state={state[:12]}...）")
    return redirect(auth_url)


@app.route("/api/logout", methods=["POST"])
def logout():
    """登出：清除本地 Session（未来可加入 Verify 端登出）"""
    session.clear()
    return jsonify({"success": True})


@app.route("/api/user", methods=["GET"])
def get_user():
    user = session.get("user")
    if user:
        return jsonify(user)
    return jsonify({"error": "Not logged in"}), 401

@app.route("/callback")
def callback():
    """OIDC 回调：使用授权码交换 Token 并创建用户会话"""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        add_log("ERROR", f"IBM Verify SSO 登录失败: {error}")
        return redirect("/?error=login_failed")

    # 从服务端 state 存储中查找，先从 session 中取（更可靠，与浏览器绑定），再从全局字典取（兼容旧流程）
    stored = None
    if state:
        sess_state = session.get("oidc_state")
        sess_verifier = session.get("oidc_code_verifier")
        if sess_state == state and sess_verifier:
            stored = {"code_verifier": sess_verifier}
            # 从 session 中清除
            session.pop("oidc_state", None)
            session.pop("oidc_code_verifier", None)
            add_log("AGENT", "State 校验: 通过（session 匹配）")
        else:
            # 降级到服务端全局字典
            stored = oidc_states.pop(state, None)
            if stored:
                add_log("AGENT", "State 校验: 通过（全局字典匹配）")
            else:
                add_log("AGENT", "State 校验: 失败（session 和全局字典均未找到 state）")
    else:
        add_log("AGENT", "State 校验: 跳过（未收到 state 参数）")

    add_log("AGENT", f"OIDC 回调: code={code[:20] if code else 'N/A'}...")

    if not code or not stored:
        add_log("ERROR", "OAuth state 校验失败")
        return redirect("/?error=invalid_state")

    try:
        # 用授权码交换 Token
        code_verifier = stored["code_verifier"]
        token_data = exchange_code_for_token(code, code_verifier)
        id_token = token_data.get("id_token")
        access_token = token_data.get("access_token")

        if not id_token:
            add_log("ERROR", "未收到 ID Token")
            return redirect("/?error=no_id_token")

        # 验证并解码 ID Token
        user_claims = verify_id_token(id_token)

        # 构建用户会话
        session["user"] = {
            "sub": user_claims.get("sub", ""),
            "email": user_claims.get("email", ""),
            "display_name": user_claims.get("name", "") or user_claims.get("display_name", "")
                         or user_claims.get("preferred_username", ""),
            "employee_id": user_claims.get("employee_id", "")
                         or user_claims.get("AA_employeeID", ""),
            "iss": user_claims.get("iss", ""),
            "uniqueSecurityName": user_claims.get("uniqueSecurityName", "")
                                 or user_claims.get("sub", ""),
            "status": user_claims.get("status", "Active"),
            "lastest_login": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        }

        # 保存 Token 供后续使用（如 OBO Token Exchange）
        session["access_token"] = access_token
        session["id_token"] = id_token

        add_log("AGENT",
                f"用户 SSO 登录成功: {session['user'].get('display_name', session['user'].get('sub', '未知'))}")
        return redirect("/")

    except Exception as e:
        add_log("ERROR", f"OIDC 回调处理失败: {str(e)}")
        return redirect("/?error=login_failed")

def exchange_code_for_token(code, code_verifier=""):
    """用授权码交换 Access Token 和 ID Token（支持 PKCE）"""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": VERIFY_REDIRECT_URI,
        "client_id": VERIFY_CLIENT_ID,
        "client_secret": VERIFY_CLIENT_SECRET,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(VERIFY_TOKEN_ENDPOINT, data=data, headers=headers)
        if resp.status_code != 200:
            raise Exception(
                f"Token exchange failed: HTTP {resp.status_code} - {resp.text[:500]}"
            )
        return resp.json()


def verify_id_token(id_token):
    """通过 JWKS 验证 ID Token 的签名并返回 claims"""
    jwks_client = get_jwks_client()
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)

    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=VERIFY_CLIENT_ID,
        issuer=f"{VERIFY_ISSUER}/oauth2",
        options={"verify_exp": True},
    )
    return claims



@app.route("/api/check-auth", methods=["GET"])
def check_auth():
    """检查登录状态"""
    user = session.get("user")
    if user:
        return jsonify({"logged_in": True, "user": user})
    return jsonify({"logged_in": False})




@app.route("/api/mcp/tools", methods=["GET"])
def get_mcp_tools():
    tools_info = [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in mcp_tools]
    return jsonify(tools_info)


@app.route("/api/mcp/tools", methods=["POST"])
def add_mcp_tool():
    data = request.get_json()
    name = data.get("name")
    description = data.get("description")
    url = data.get("url", "")
    if not name or not description:
        return jsonify({"success": False, "message": "名称和描述不能为空"}), 400

    async def external_handler(args):
        add_log("AGENT", f"调用外部 MCP 工具: {name}", json.dumps({"url": url, "args": args}, ensure_ascii=False))
        return json.dumps({
            "tool": name, "status": "simulated",
            "message": f"工具 {name} 调用成功（模拟返回）",
        }, ensure_ascii=False)

    params = data.get("parameters", {"type": "object", "properties": {"input": {"type": "string", "description": "输入参数"}}, "required": ["input"]})
    register_mcp_tool(name, description, params, external_handler)
    add_log("AGENT", f"注册新 MCP 工具: {name}")
    return jsonify({"success": True, "tool": {"name": name, "description": description}})


@app.route("/api/mcp/tools/<name>", methods=["DELETE"])
def delete_mcp_tool(name):
    global mcp_tools, mcp_tool_definitions
    mcp_tools = [t for t in mcp_tools if t["name"] != name]
    mcp_tool_definitions = [d for d in mcp_tool_definitions if d["function"]["name"] != name]
    add_log("AGENT", f"删除 MCP 工具: {name}")
    return jsonify({"success": True})


@app.route("/api/agent/logs", methods=["GET"])
def get_agent_logs():
    return jsonify(agent_logs[-200:])


@app.route("/api/agent/logs", methods=["DELETE"])
def clear_agent_logs():
    agent_logs.clear()
    return jsonify({"success": True})


# ====== WebSocket 聊天 ======

@socketio.on("chat_message")
def handle_chat_message(data):
    user = session.get("user", {})
    user_message = data.get("message", "")
    history = data.get("history", [])

    if not user_message:
        return

    add_log("AGENT", f"收到用户消息", user_message[:200])

    messages = [{"role": "system", "content": "你是一个有用的 AI 助手。你可以使用提供的工具来帮助用户。\n"
                                              "【关于删除操作】当用户要求删除订单时，直接调用 delete_order(order_id=\"xxx\") 即可，不要先调用 get_api_token_from_vault。系统会自动弹出安全验证界面让用户授权并获取 Token。\n"
                                              "【关于展示信息】展示订单列表时用简洁的中文文本总结，每条订单用短横线一行列出，不要使用 Markdown 表格，不要使用任何 emoji 符号。\n"
                                              "对于工具调用的结果，请用中文回复用户。"}]

    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    try:
        response_data = call_opencode(messages)

        if "error" in response_data:
            error_msg = response_data["error"]
            add_log("ERROR", f"OpenCode Go API 错误", error_msg)
            emit("chat_response", {"role": "assistant", "content": f"抱歉，调用 AI 服务时出错：{error_msg}"})
            return

        assistant_content = response_data.get("content", "")
        tool_calls = response_data.get("tool_calls", [])

        if tool_calls:
            emit("chat_response", {
                "role": "assistant",
                "content": assistant_content or "正在调用工具处理您的请求...",
                "tool_calls": [{"name": tc["function"]["name"], "arguments": json.loads(tc["function"].get("arguments", "{}"))} for tc in tool_calls],
            })

            # ====== 添加 AI 助手消息（包含本次所有 tool_calls，API 要求合并成一条） ======
            if tool_calls:
                messages.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args_str = tc["function"].get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                # add_log("AGENT", f"执行工具: {fn_name}", json.dumps(fn_args, ensure_ascii=False))

                # ====== 敏感操作检查 ======
                if fn_name == "delete_order":
                    verify_id = str(uuid.uuid4())[:8]
                    event = threading.Event()
                    pending_verifications[verify_id] = {"approved": None, "event": event, "order_id": fn_args.get("order_id", "")}

                    time.sleep(0.8)  # 模拟验证请求准备过程，让 Verify 面板有真实感
                    emit("request_verify", {
                        "verify_id": verify_id,
                        "order_id": fn_args.get("order_id", ""),
                        "tool": "delete_order",
                        "message": f"AI 助手请求删除订单 {fn_args.get('order_id', '')}，请确认。"
                    })

                    # 等待用户确认（最长 120 秒）
                    event.wait(timeout=120.0)

                    approved = pending_verifications.get(verify_id, {}).get("approved")
                    if approved:
                        time.sleep(1.0)  # 模拟用户授权后的处理延迟
                        # 用户确认后，再从 Vault 获取删除权限 Token
                        if not fn_args.get("api_token"):
                            add_log("AGENT", "用户已授权")
                            vault_token_op = ensure_vault_token()
                            vault_result = get_api_token_from_vault("delete", vault_token_op)
                            if vault_result.get("status") == "SUCCESS":
                                fn_args["api_token"] = vault_result["api_token"]
                            else:
                                add_log("ERROR", "获取 API Token 失败，无法执行删除")
                                tool_result = json.dumps({"error": "获取 API Token 失败"}, ensure_ascii=False)

                        if fn_args.get("api_token"):
                            add_log("AGENT", f"调用 delete_order 服务删除订单: {fn_args.get('order_id', '')}")
                            loop = asyncio_new()
                            loop.run_until_complete(_handle_delete_order(fn_args))
                            loop.close()
                            tool_result = json.dumps({"success": True, "message": f"订单 {fn_args.get('order_id', '')} 已成功删除（用户已授权）"},
                                                     ensure_ascii=False)
                    else:
                        add_log("WARN", "用户拒绝授权，取消删除操作")
                        tool_result = json.dumps({"cancelled": True, "message": "用户取消了删除操作"}, ensure_ascii=False)

                    if verify_id in pending_verifications:
                        del pending_verifications[verify_id]
                else:
                    # 普通工具执行
                    tool_result = None
                    for tool in mcp_tools:
                        if tool["name"] == fn_name:
                            loop = asyncio_new()
                            tool_result = loop.run_until_complete(tool["handler"](fn_args))
                            loop.close()
                            break

                if tool_result is None:
                    tool_result = json.dumps({"error": f"未知工具: {fn_name}"})

                # add_log("AGENT", f"工具执行完成: {fn_name}", tool_result[:500])

                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": tool_result})

            # 循环处理 AI 可能继续产生的工具调用（如获取 token 后再调用 list_orders）
            while True:
                final_response = call_opencode(messages)
                if "error" in final_response:
                    add_log("AGENT", "AI 助手回复（出错）", final_response['error'][:300])
                    emit("chat_response", {"role": "assistant", "content": f"处理完成（API 返回错误: {final_response['error']}）", "tool_results": True})
                    break

                final_tool_calls = final_response.get("tool_calls", [])
                if not final_tool_calls:
                    add_log("AGENT", "AI 助手回复", final_response.get("content", "处理完成。")[:500])
                    emit("chat_response", {"role": "assistant", "content": final_response.get("content", "处理完成。"), "tool_results": True})
                    break

                # ====== 添加本轮 AI 助手消息（包含 tool_calls），之后才能追加 tool result ======
                new_content = final_response.get("content", "")
                messages.append({"role": "assistant", "content": new_content, "tool_calls": final_tool_calls})
                # 先展示 AI 的中间思考结果（如 "已获取 Token，现在查询订单"）
                emit("chat_response", {
                    "role": "assistant",
                    "content": new_content,
                    "tool_calls": [{"name": tc["function"]["name"], "arguments": json.loads(tc["function"].get("arguments", "{}"))} for tc in final_tool_calls]
                })

                # 继续处理新一轮工具调用
                for tc in final_tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args_str = tc["function"].get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_str)
                    except json.JSONDecodeError:
                        fn_args = {}

                    # add_log("AGENT", f"执行工具: {fn_name}", json.dumps(fn_args, ensure_ascii=False))

                    # ====== 敏感操作检查 ======
                    if fn_name == "delete_order":
                        verify_id = str(uuid.uuid4())[:8]
                        event = threading.Event()
                        pending_verifications[verify_id] = {"approved": None, "event": event, "order_id": fn_args.get("order_id", "")}

                        time.sleep(0.8)  # 模拟验证请求准备过程，让 Verify 面板有真实感
                        emit("request_verify", {
                            "verify_id": verify_id,
                            "order_id": fn_args.get("order_id", ""),
                            "tool": "delete_order",
                            "message": f"AI 助手请求删除订单 {fn_args.get('order_id', '')}，请确认。"
                        })

                        event.wait(timeout=120.0)

                        approved = pending_verifications.get(verify_id, {}).get("approved")
                        if approved:
                            time.sleep(1.0)  # 模拟用户授权后的处理延迟
                            # 用户确认后，再从 Vault 获取删除权限 Token
                            if not fn_args.get("api_token"):
                                add_log("AGENT", "用户已授权，正在从 Vault 获取删除权限 Token")
                                vault_token_op = ensure_vault_token()
                                vault_result = get_api_token_from_vault("delete", vault_token_op)
                                if vault_result.get("status") == "SUCCESS":
                                    fn_args["api_token"] = vault_result["api_token"]
                                else:
                                    add_log("ERROR", "获取 API Token 失败，无法执行删除")
                                    tool_result = json.dumps({"error": "获取 API Token 失败"}, ensure_ascii=False)

                            if fn_args.get("api_token"):
                                add_log("AGENT", f"调用 delete_order 服务删除订单: {fn_args.get('order_id', '')}")
                                loop = asyncio_new()
                                loop.run_until_complete(_handle_delete_order(fn_args))
                                loop.close()
                                tool_result = json.dumps({"success": True, "message": f"订单 {fn_args.get('order_id', '')} 已成功删除（用户已授权）"},
                                                         ensure_ascii=False)
                        else:
                            add_log("WARN", "用户拒绝授权，取消删除操作")
                            tool_result = json.dumps({"cancelled": True, "message": "用户取消了删除操作"}, ensure_ascii=False)

                        if verify_id in pending_verifications:
                            del pending_verifications[verify_id]
                    else:
                        tool_result = None
                        for tool in mcp_tools:
                            if tool["name"] == fn_name:
                                loop = asyncio_new()
                                tool_result = loop.run_until_complete(tool["handler"](fn_args))
                                loop.close()
                                break

                    if tool_result is None:
                        tool_result = json.dumps({"error": f"未知工具: {fn_name}"})

                    # add_log("AGENT", f"工具执行完成: {fn_name}", tool_result[:500])
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": tool_result})

                # 循环继续——让 AI 基于工具结果生成下一轮回复（可能是最终文本或更多工具调用）
        else:
            add_log("AGENT", "AI 助手回复", assistant_content[:500])
            emit("chat_response", {"role": "assistant", "content": assistant_content})

    except Exception as e:
        error_detail = str(e)
        add_log("ERROR", "聊天处理异常", error_detail)
        emit("chat_response", {"role": "assistant", "content": f"抱歉，处理您的消息时出现错误：{error_detail}"})


@socketio.on("verify_response")
def handle_verify_response(data):
    """处理用户在 verify3 中的确认/拒绝响应"""
    verify_id = data.get("verify_id")
    approved = data.get("approved", False)
    if verify_id in pending_verifications:
        pending_verifications[verify_id]["approved"] = approved
        pending_verifications[verify_id]["event"].set()
        status = "已授权" if approved else "已拒绝"
        add_log("AGENT", f"用户响应验证请求 {verify_id}: {status}")


def asyncio_new():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def call_opencode(messages):
    """调用 OpenCode Go API"""
    api_key = OPENCODE_API_KEY
    if not api_key:
        return {"error": "未配置 OpenCode Go API Key，请在环境变量中设置 OPENCODE_API_KEY"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": OPENCODE_MODEL,
        "messages": messages,
        "tools": mcp_tool_definitions if mcp_tool_definitions else None,
        "tool_choice": "auto" if mcp_tool_definitions else None,
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(f"{OPENCODE_BASE_URL}/chat/completions", headers=headers, json=payload)
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.text[:300]}"}

            result = response.json()
            choice = result["choices"][0]
            message = choice["message"]

            return {"content": message.get("content", ""), "tool_calls": message.get("tool_calls", [])}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print("=" * 50)
    print("  AI 智能助手 v1.1")
    print("  启动: http://127.0.0.1:18923")
    print("=" * 50)
    socketio.run(app, host="127.0.0.1", port=18923, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
