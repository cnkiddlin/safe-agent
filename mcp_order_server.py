"""
MCP Server — 订单管理
提供两个接口：
  1. GET  /api/orders          — 查看所有订单
  2. DELETE /api/orders/<id>   — 删除指定订单
启动方式：python mcp_order_server.py
"""

import json
import uuid
import os
import hvac
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

order_app = Flask(__name__)

# ====== 模拟订单数据 ======
ORDERS = [
    {
        "id": "ORD-20260701-001",
        "customer": "李四",
        "product": "Bob Premium Plan",
        "amount": 100.00,
        "status": "已完成",
        "created_at": "2026-07-01 09:30:00",
        "updated_at": "2026-07-02 14:20:00",
    },
    {
        "id": "ORD-20260701-002",
        "customer": "李四",
        "product": "Bob Enterprise Plan",
        "amount": 600,
        "status": "已完成",
        "created_at": "2026-07-02 08:00:00",
        "updated_at": "2026-07-02 16:45:00",
    },
    {
        "id": "ORD-20260702-003",
        "customer": "王五",
        "product": "Bob Premium Plan",
        "amount": 100,
        "status": "待付款",
        "created_at": "2026-07-01 11:15:00",
        "updated_at": "2026-07-01 11:15:00",

    },
]


def get_api_token_from_vault(operation_name: str, ):
    vault_addr = 'http://127.0.0.1:8200'
    vault_token = os.getenv("VAULT_TOKEN", "<your-vault-token>")

    if operation_name not in ("get", "delete"):
        return {
            "status": "FAILED",
            "message": "operation_name must be GET or DELETE"
        }

        # Create Vault client
    client = hvac.Client(
        url=vault_addr,
        token=vault_token,
    )
    if not client.is_authenticated():
        return {
            "status": "FAILED",
            "message": "Failed to authenticate with Vault"
        }

        # Read API token from KV Secret Engine
    response = client.secrets.kv.v1.read_secret(
        mount_point="secret",
        path=f"order/user_zhangsan/{operation_name}",
    )
    api_token = response["data"][f"{operation_name}_api_token"]

    return {
        "status": "SUCCESS",
        "operation": operation_name,
        "api_token": api_token,
    }


@order_app.route("/api/orders", methods=["GET"])
def list_orders():
    """返回所有订单"""
    api_token = request.args.get("api_token", "")

    # 验证 api_token
    if api_token != 'QUERY_API_TOKEN_DEMO_123456':
        return {'error': 'Invalid API token'}

    # 支持可选的状态过滤
    status_filter = request.args.get("status")
    if status_filter:
        filtered = [o for o in ORDERS if o["status"] == status_filter]
        return jsonify({"success": True, "total": len(filtered), "orders": filtered})
    return jsonify({"success": True, "total": len(ORDERS), "orders": ORDERS})


@order_app.route("/api/orders/<order_id>", methods=["DELETE"])
def delete_order(order_id):
    """删除指定订单"""
    api_token = request.args.get("api_token", "")
    # 验证 api_token
    if api_token != 'DELETE_API_TOKEN_DEMO_123456':
        return {'error': 'Invalid API token'}

    # 删除订单
    global ORDERS
    before = len(ORDERS)
    ORDERS = [o for o in ORDERS if o["id"] != order_id]
    if len(ORDERS) < before:
        return jsonify({
            "success": True,
            "message": f"订单 {order_id} 已删除",
            "deleted_id": order_id,
        })
    else:
        return jsonify({
            "success": False,
            "message": f"未找到订单 {order_id}",
        }), 404


if __name__ == "__main__":
    print("=" * 50)
    print("  MCP Order Server")
    print("  地址: http://127.0.0.1:18724")
    print("  接口:")
    print("    GET    /api/orders          — 查看订单")
    print("    DELETE /api/orders/<id>     — 删除订单")
    print("=" * 50)
    order_app.run(host="127.0.0.1", port=18724, debug=False)
