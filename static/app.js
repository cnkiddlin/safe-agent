// ====== Socket.IO ======
let socket = null;
let chatHistory = [];
let isProcessing = false;
let currentVerifyId = null;

// ====== 页面加载时检查登录状态 ======
document.addEventListener('DOMContentLoaded', () => {
    // 检查是否刚从 verify2 跳转过来（携带 login=success 参数）
    const params = new URLSearchParams(window.location.search);
    if (params.get('login') === 'success') {
        // 尝试验证 session
        checkAuthAndShowApp();
    }
    // 自动高度调整
    const inp = document.getElementById('chatInput');
    if (inp) inp.addEventListener('input', function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,100)+'px';});
});

async function checkAuthAndShowApp() {
    try {
        const res = await fetch('/api/check-auth');
        const data = await res.json();
        if (data.logged_in) {
            document.getElementById('loginPage').style.display = 'none';
            document.getElementById('mainApp').style.display = 'flex';
            initApp();
        }
    } catch (e) {
        console.error('Auth check failed', e);
    }
}

// ====== 登录（跳转到 IBM Verify 流程） ======
async function handleLogin() {
    // 跳转到 verify1 页面进行 passkey 验证
    window.location.href = '/verify1';
}

// ====== 初始化 ======
function initApp() {
    socket = io();

    socket.on("connect", () => {
        // 连接后清除 URL 参数
        if (window.history.replaceState && window.location.search) {
            window.history.replaceState({}, '', '/');
        }
    });

    socket.on("chat_response", (data) => {
        hideTyping();
        appendMessage(data.role, data.content, data.tool_calls, data.tool_results);
        if (data.role === "assistant") {
            chatHistory.push({ role: "assistant", content: data.content });
        }
        isProcessing = false;
        updateSendBtn();
    });

    socket.on("agent_log", (logEntry) => {
        appendLog(logEntry);
    });

    // ====== 监听敏感操作验证请求 ======
    socket.on("request_verify", (data) => {
        currentVerifyId = data.verify_id;
        showVerify3Overlay(data);
    });

    loadMCPTools();
}

// ====== Verify3 Overlay ======
function showVerify3Overlay(data) {
    const overlay = document.getElementById('verify3Overlay');
    const iframe = document.getElementById('verify3Iframe');

    // 设置 iframe 的 action 参数
    const action = data.tool === 'delete_order'
        ? `Delete Order ${data.order_id || ''}`
        : data.tool || 'Sensitive Operation';
    iframe.src = `/verify3?action=${encodeURIComponent(action)}`;

    overlay.classList.add('show');

    // 监听 iframe 的 message
    window.addEventListener('message', handleVerify3Message);
}

function handleVerify3Message(event) {
    if (event.data && event.data.type === 'verify3_response') {
        const approved = event.data.approved;
        window.removeEventListener('message', handleVerify3Message);
        closeVerify3Overlay();

        if (currentVerifyId && socket) {
            socket.emit('verify_response', {
                verify_id: currentVerifyId,
                approved: approved
            });
            currentVerifyId = null;
        }
    }
}

function closeVerify3Overlay() {
    const overlay = document.getElementById('verify3Overlay');
    overlay.classList.remove('show');
    const iframe = document.getElementById('verify3Iframe');
    // 重置 iframe
    setTimeout(() => { iframe.src = '/verify3'; }, 200);
}

// ====== 发送消息 ======
function sendMessage() {
    const input = document.getElementById("chatInput");
    const msg = input.value.trim();
    if (!msg || isProcessing) return;

    input.value = "";
    input.style.height = "auto";

    appendMessage("user", msg);
    chatHistory.push({ role: "user", content: msg });

    showTyping();
    isProcessing = true;
    updateSendBtn();
    socket.emit("chat_message", {
        message: msg,
        history: chatHistory.slice(-20),
    });
}

function handleInputKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 100) + "px";
}

function updateSendBtn() {
    document.getElementById("sendBtn").disabled = isProcessing;
}

// ====== 消息渲染 ======
function appendMessage(role, content) {
    const container = document.getElementById("chatMessages");
    const div = document.createElement("div");
    div.className = `message ${role}`;

    const avatar = document.createElement("div");
    avatar.className = `message-avatar avatar-${role}`;
    avatar.innerHTML = role === "user"
        ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`
        : `<svg width="18" height="18" viewBox="0 0 48 48" fill="none"><circle cx="24" cy="24" r="22" stroke="#0066CC" stroke-width="2" fill="#EFF6FF"/><path d="M16 24L22 30L32 18" stroke="#0066CC" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const cd = document.createElement("div");
    cd.className = "message-content";
    cd.innerHTML = formatContent(content);

    div.appendChild(avatar);
    div.appendChild(cd);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function formatContent(text) {
    if (!text) return "";
    let h = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) => `<pre><code class="lang-${l}">${c.trim()}</code></pre>`);
    h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
    h = h.replace(/\n/g, "<br>");
    h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return h;
}

// ====== 打字指示 ======
function showTyping() {
    const c = document.getElementById("chatMessages");
    const d = document.createElement("div"); d.className = "typing-indicator"; d.id = "typingIndicator";
    d.innerHTML = `<div class="avatar-loading"></div><div class="typing-dots"><span></span><span></span><span></span></div>`;
    c.appendChild(d); c.scrollTop = c.scrollHeight;
}
function hideTyping() { const e = document.getElementById("typingIndicator"); if (e) e.remove(); }

// ====== 面板切换 ======
function togglePanel(id) {
    const panel = document.getElementById("sidePanel");
    const all = ["mcpPanel","logPanel","userPanel"];
    const t = document.getElementById(id);
    if (panel.classList.contains("open") && t.style.display !== "none") {
        panel.classList.remove("open");
        all.forEach(p => document.getElementById(p).style.display = "none");
        return;
    }
    all.forEach(p => document.getElementById(p).style.display = p === id ? "flex" : "none");
    panel.classList.add("open");
}
function toggleMCP() { togglePanel("mcpPanel"); }
function toggleLogs() { togglePanel("logPanel"); }
function toggleUserInfo() { loadUserInfo(); togglePanel("userPanel"); }

// ====== MCP ======
async function loadMCPTools() {
    try {
        const r = await fetch("/api/mcp/tools");
        const tools = await r.json();
        const list = document.getElementById("mcpToolList");
        list.innerHTML = "";
        if (!tools.length) {
            list.innerHTML = '<p style="color:var(--text-tertiary);font-size:12px;text-align:center;padding:20px;">暂无工具</p>';
            return;
        }
        tools.forEach(t => {
            const d = document.createElement("div"); d.className = "tool-item";
            d.innerHTML = `
                <div class="tool-item-info">
                    <div class="tool-item-name">${esc(t.name)}</div>
                    <div class="tool-item-desc">${esc(t.description)}</div>
                </div>
                <button class="tool-item-del" onclick="deleteMCPTool('${t.name}')">✕</button>`;
            list.appendChild(d);
        });
    } catch (e) { console.error(e); }
}

async function addMCPTool() {
    const name = document.getElementById("mcpName").value.trim();
    const url = document.getElementById("mcpUrl").value.trim();
    const desc = document.getElementById("mcpDesc").value.trim();
    if (!name || !desc) { showToast("名称和描述不能为空", "warning"); return; }
    try {
        const r = await fetch("/api/mcp/tools", { method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({name,description,url}) });
        const d = await r.json();
        if (d.success) {
            showToast(`已添加 ${name}`, "success");
            document.getElementById("mcpName").value = "";
            document.getElementById("mcpUrl").value = "";
            document.getElementById("mcpDesc").value = "";
            loadMCPTools();
        } else { showToast(d.message, "error"); }
    } catch (e) { showToast("网络错误", "error"); }
}

async function deleteMCPTool(name) {
    if (!confirm(`确定删除 "${name}" ？`)) return;
    try {
        await fetch(`/api/mcp/tools/${encodeURIComponent(name)}`, {method:"DELETE"});
        showToast("已删除", "success");
        loadMCPTools();
    } catch (e) { showToast("删除失败", "error"); }
}

// ====== 日志 ======
function appendLog(e) {
    const list = document.getElementById("logList");
    const d = document.createElement("div"); d.className = "log-entry";
    d.onclick = function(){this.classList.toggle("expanded")};
    d.innerHTML = `<span class="log-time">${esc(e.timestamp)}</span><span class="log-level ${e.level}">${e.level}</span><span class="log-msg">${esc(e.message)}</span>${e.detail ? `<div class="log-detail">${esc(e.detail)}</div>` : ""}`;
    list.appendChild(d);
    document.getElementById("logPanelBody").scrollTop = document.getElementById("logPanelBody").scrollHeight;
}
async function clearLogs() {
    await fetch("/api/agent/logs", {method:"DELETE"});
    document.getElementById("logList").innerHTML = "";
    showToast("日志已清空", "success");
}

// ====== 用户信息 ======
async function loadUserInfo() {
    try {
        const r = await fetch("/api/user");
        const u = await r.json();
        document.getElementById("display_name").textContent = u.display_name || "-";
        document.getElementById("employee_id").textContent = u.employee_id || "-";
        document.getElementById("email").textContent = u.email || "-";
        document.getElementById("jwt_issuer").textContent = u.iss || "-";
        document.getElementById("jwt_subject").textContent = u.sub || "-";
        document.getElementById("status").textContent = u.status || "-";
        document.getElementById("lastest_login").textContent = u.lastest_login || "-";
    } catch (e) { console.error(e); }
}

// ====== 退出 ======
async function logout() {
    if (!confirm("确定退出？")) return;
    await fetch("/api/logout", {method:"POST"});
    if (socket) socket.disconnect();
    location.reload();
}

// ====== 新建对话 ======
function newChat() {
    if (chatHistory.length === 0) return;
    if (!confirm("确定要清空当前对话吗？")) return;
    chatHistory = [];
    document.getElementById("chatMessages").innerHTML = "";
    const container = document.getElementById("chatMessages");
    const div = document.createElement("div");
    div.className = "message welcome-message";
    div.innerHTML = `
        <div class="message-avatar avatar-assistant">
            <svg width="18" height="18" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="24" r="22" stroke="#0066CC" stroke-width="2" fill="#EFF6FF"/>
                <path d="M16 24L22 30L32 18" stroke="#0066CC" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        </div>
        <div class="message-content">
            <p>您好！我是 AI 智能助手，请随意向我提问。</p>
            <p class="msg-hint">我可以查天气、算算术、查时间 — 还能帮您管理订单。</p>
        </div>`;
    container.appendChild(div);
    showToast("已开启新对话", "success");
}

// ====== 工具 ======
function esc(t) { if (!t) return ""; const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }

function showToast(msg, type) {
    const old = document.querySelector(".toast"); if (old) old.remove();
    const t = document.createElement("div"); t.className = "toast";
    t.textContent = msg;
    const bg = {success:"#ECFDF5",error:"#FEF2F2",warning:"#FFFBEB",info:"#EFF6FF"};
    const fg = {success:"#059669",error:"#DC2626",warning:"#D97706",info:"#0066CC"};
    const c = bg[type]||bg.info, f = fg[type]||fg.info;
    Object.assign(t.style, {
        position:"fixed",top:"20px",left:"50%",transform:"translateX(-50%)",
        padding:"8px 20px",borderRadius:"var(--radius-md)",fontSize:"13px",
        zIndex:"9999",boxShadow:"0 4px 16px rgba(0,0,0,0.1)",
        background:c,color:f,border:`1px solid ${f}20`,
        animation:"msgIn 0.25s ease-out",transition:"opacity 0.25s",
    });
    document.body.appendChild(t);
    setTimeout(()=>t.style.opacity="0", 2000);
    setTimeout(()=>t.remove(), 2500);
}
