#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import urllib.parse
import logging
from functools import wraps
from typing import List, Dict, Optional, Any

import requests
import urllib3

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 配置（多地址容灾） ====================
AUTH_SERVERS = [
    "https://154.17.1.102/realms/vpn_application/protocol/openid-connect/token",
    "https://kc.huozhong.us/realms/vpn_application/protocol/openid-connect/token"
]
API_SERVERS = [
    "https://154.17.0.133/api/nodesystem/user",
    "https://api.huozhong.us/api/nodesystem/user"
]

# 从环境变量读取敏感信息
USERNAME = os.environ.get("HZ_USERNAME")
PASSWORD = os.environ.get("HZ_PASSWORD")
CLIENT_ID = os.environ.get("HZ_CLIENT_ID", "vpn-user")
CLIENT_SECRET = os.environ.get("HZ_CLIENT_SECRET")

# 输出文件
OUTPUT_FILE = "huozhong_vless_links.txt"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ==================== 会话与重试装饰器 ====================
session = requests.Session()
session.verify = False

def retry_request(max_retries: int = 4, backoff_factor: float = 2.0, exceptions=(requests.RequestException,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        logger.error(f"重试 {max_retries} 次后失败: {e}")
                        raise
                    wait = backoff_factor ** (attempt + 1) * (0.5 + 0.5 * (time.time() % 1))
                    logger.warning(f"请求失败 (尝试 {attempt+1}/{max_retries+1}): {e}，等待 {wait:.2f}s 后重试")
                    time.sleep(wait)
        return wrapper
    return decorator

# ==================== 登录（多地址容灾） ====================
def login_and_get_token() -> Optional[str]:
    logger.info("正在获取新 Token...")
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "password",
        "username": USERNAME,
        "password": PASSWORD,
    }
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "accept-charset": "UTF-8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    for auth_url in AUTH_SERVERS:
        try:
            resp = session.post(auth_url, data=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token")
                if token:
                    logger.info(f"Token 获取成功，有效期 {data.get('expires_in', 0)//60} 分钟")
                    return token
                else:
                    logger.error(f"响应中无 access_token: {auth_url}")
            else:
                logger.warning(f"认证服务器 {auth_url} 返回状态码 {resp.status_code}")
        except Exception as e:
            logger.warning(f"连接认证服务器 {auth_url} 异常: {e}")
    logger.error("所有认证服务器均失败")
    return None

# ==================== 节点列表（多地址容灾） ====================
@retry_request(max_retries=4, backoff_factor=2.0)
def get_node_list(token: str) -> List[Dict]:
    logger.info("正在获取节点列表...")
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "authorization": f"Bearer {token}",
        "accept-charset": "UTF-8",
        "Content-Type": "application/json",
    }
    for api_url in API_SERVERS:
        url = f"{api_url}/nodeList?platform=android"
        try:
            resp = session.post(url, headers=headers, json={}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    logger.info(f"成功获取 {len(data)} 个节点")
                    return data
                else:
                    logger.error(f"响应不是列表格式: {api_url}")
            else:
                logger.warning(f"API 服务器 {api_url} 返回状态码 {resp.status_code}")
        except Exception as e:
            logger.warning(f"连接 API 服务器 {api_url} 异常: {e}")
    raise Exception("所有 API 服务器均无法获取节点列表")

# ==================== 节点配置（多地址容灾） ====================
@retry_request(max_retries=4, backoff_factor=2.0)
def get_client_config(node_id: int, token: str) -> Optional[Dict]:
    payload = {"nodeId": node_id}
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "authorization": f"Bearer {token}",
        "accept-charset": "UTF-8",
        "Content-Type": "application/json",
    }
    for api_url in API_SERVERS:
        url = f"{api_url}/clientConfig"
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"节点 {node_id} 从 {api_url} 获取配置失败: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"节点 {node_id} 连接 {api_url} 异常: {e}")
    raise Exception(f"所有 API 服务器均无法获取节点 {node_id} 的配置")

# ==================== 节点名称提取 ====================
def extract_node_name(node: Dict) -> str:
    """从节点信息提取名称（与火种.py一致，更丰富）"""
    name_parts = []
    if region := node.get("regionNameCn"):
        name_parts.append(region.strip())
    if name := node.get("nameCn"):
        name_parts.append(name.strip())
    elif name := node.get("nameEn"):
        name_parts.append(name.strip())
    if tag := node.get("tagCn"):
        name_parts.append(f"({tag.strip()})")
    return " - ".join(name_parts) if name_parts else f"Node-{node.get('nodeId', '未知')}"

# ==================== 链接生成（包含 WS 的 path/host，无 alpn） ====================
def generate_vless_link(config: Dict, node_name: str) -> str:
    """生成 VLESS 链接，支持 reality/tls，包含 WebSocket 的 path 和 host"""
    vnext = config["settings"]["vnext"][0]
    user = vnext["users"][0]
    stream = config["streamSettings"]
    network = stream.get("network", "tcp")

    params = {
        "encryption": user.get("encryption", "none"),
        "type": network,
    }

    if stream.get("security") == "reality":
        reality = stream.get("realitySettings", {})
        params.update({
            "security": "reality",
            "pbk": reality.get("publicKey", ""),
            "fp": reality.get("fingerprint", "chrome"),
            "sni": reality.get("serverName", ""),
            "sid": reality.get("shortId", ""),
            "headerType": "none",
        })
    elif stream.get("security") == "tls":
        tls = stream.get("tlsSettings", {})
        params["security"] = "tls"
        if sni := tls.get("serverName"):
            params["sni"] = sni
        if tls.get("allowInsecure") is not None:
            params["allowInsecure"] = "1" if tls.get("allowInsecure") else "0"
        if fp := tls.get("fingerprint"):
            params["fp"] = fp
        # 不添加 alpn

    if network == "ws":
        ws = stream.get("wsSettings", {})
        if path := ws.get("path"):
            params["path"] = path
        # host 取自 sni，或默认
        params["host"] = params.get("sni", "vpn-node.internal")

    query = urllib.parse.urlencode(params)
    remark = urllib.parse.quote(node_name)
    return f"vless://{user['id']}@{vnext['address']}:{vnext['port']}?{query}#{remark}"

def generate_trojan_link(config: Dict, node_name: str) -> str:
    """生成 Trojan 链接，包含 WS 的 path/host 和 grpc 的 serviceName"""
    servers = config.get("settings", {}).get("servers", [])
    if not servers:
        raise ValueError("配置中缺少 servers 字段")
    server = servers[0]
    address = server.get("address")
    port = server.get("port")
    password = server.get("password")
    if not all([address, port, password]):
        raise ValueError("Trojan 服务器缺少 address/port/password")

    stream = config.get("streamSettings", {})
    network = stream.get("network", "tcp")
    security = stream.get("security", "")
    tls_settings = stream.get("tlsSettings", {})
    ws_settings = stream.get("wsSettings", {})
    grpc_settings = stream.get("grpcSettings", {})

    params = {}
    if security == "tls":
        params["security"] = "tls"
        if sni := tls_settings.get("serverName"):
            params["sni"] = sni
        if tls_settings.get("allowInsecure") is not None:
            params["allowInsecure"] = "1" if tls_settings.get("allowInsecure") else "0"
        if fp := tls_settings.get("fingerprint"):
            params["fp"] = fp
        # 不添加 alpn

    params["type"] = network
    if network == "ws":
        if path := ws_settings.get("path"):
            params["path"] = path
        params["host"] = tls_settings.get("serverName", "vpn-node.internal")
    elif network == "grpc" and (svc := grpc_settings.get("serviceName")):
        params["serviceName"] = svc

    query = urllib.parse.urlencode(params)
    remark = urllib.parse.quote(node_name)
    return f"trojan://{password}@{address}:{port}?{query}#{remark}"

def save_link(link: str):
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{link}\n")

# ==================== 主程序 ====================
def main():
    if not all([USERNAME, PASSWORD, CLIENT_SECRET]):
        logger.error("缺少必需的环境变量: HZ_USERNAME, HZ_PASSWORD, HZ_CLIENT_SECRET")
        sys.exit(1)

    # 清空输出文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        pass

    logger.info("火种VPN - 自动登录 + 提取链接 (支持 VLESS / Trojan，完整 WS 支持)")
    logger.info(f"输出文件: {OUTPUT_FILE}")

    token = login_and_get_token()
    if not token:
        logger.error("登录失败，终止")
        sys.exit(1)

    try:
        nodes = get_node_list(token)
    except Exception as e:
        logger.error(f"获取节点列表失败: {e}")
        sys.exit(1)

    if not nodes:
        logger.warning("节点列表为空，退出")
        sys.exit(1)

    success_count = 0
    for node in nodes:
        node_id = node.get("nodeId")
        if not node_id:
            continue
        node_name = extract_node_name(node)

        try:
            config = get_client_config(node_id, token)
        except Exception as e:
            logger.error(f"获取节点 {node_id} 配置失败: {e}")
            continue

        if not config:
            continue

        protocol = config.get("protocol", "").lower()
        link = None
        try:
            if protocol == "vless":
                link = generate_vless_link(config, node_name)
            elif protocol == "trojan":
                link = generate_trojan_link(config, node_name)
            else:
                logger.debug(f"跳过不支持的协议 {protocol} (node {node_id})")
                continue
        except Exception as e:
            logger.error(f"生成 {protocol} 链接失败 (node {node_id}): {e}")
            continue

        if link:
            save_link(link)
            success_count += 1
            logger.info(f"已保存 {protocol.upper()} 节点 {node_id} ({node_name}) → {link[:60]}...")

    logger.info(f"完成！共保存 {success_count} 条链接")
    if success_count == 0:
        logger.error("未生成任何有效链接，视为失败")
        sys.exit(1)

if __name__ == "__main__":
    main()
