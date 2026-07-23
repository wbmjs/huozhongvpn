#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import requests
import json
import urllib.parse
import time
import urllib3
from requests.exceptions import RequestException
from typing import List, Dict, Optional

# 禁用 SSL 警告（因为使用 verify=False）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 配置区（从环境变量读取） ====================
BASE_URL = "https://server1a.hzzf.cc/api/nodesystem/user"
AUTH_URL = "https://server2k.hzzf.cc/realms/vpn_application/protocol/openid-connect/token"

# 从环境变量获取敏感信息
USERNAME = os.environ.get("HZ_USERNAME")
PASSWORD = os.environ.get("HZ_PASSWORD")
CLIENT_ID = os.environ.get("HZ_CLIENT_ID", "vpn-user")  # 保留默认值
CLIENT_SECRET = os.environ.get("HZ_CLIENT_SECRET")

# 检查必需的环境变量
if not all([USERNAME, PASSWORD, CLIENT_SECRET]):
    print("错误：缺少必需的环境变量 HZ_USERNAME, HZ_PASSWORD, HZ_CLIENT_SECRET")
    sys.exit(1)

# 输出文件（使用相对路径，适配 Ubuntu 运行环境）
OUTPUT_FILE = "huozhong_vless_links.txt"

# ==================== SSL 配置 ====================
session = requests.Session()
session.verify = False


def login_and_get_token() -> Optional[str]:
    """使用用户名密码登录，获取新的 Bearer Token"""
    print("正在尝试登录获取新 Token...")
    
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "password",
        "username": USERNAME,
        "password": PASSWORD
    }
    
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "accept-charset": "UTF-8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    
    try:
        resp = session.post(AUTH_URL, data=payload, headers=headers, timeout=15)
        print(f"登录状态码: {resp.status_code}")
        
        if resp.status_code != 200:
            print(f"登录失败: {resp.text[:300]}")
            return None
        
        data = resp.json()
        token = data.get("access_token")
        if not token:
            print("响应中无 access_token")
            return None
        
        expires_in = data.get("expires_in", 0)
        print(f"登录成功！新 Token 获取成功，有效期约 {expires_in//60} 分钟")
        return token
    
    except Exception as e:
        print(f"登录异常: {str(e)}")
        return None


def get_node_list(token: str, max_retries: int = 4, backoff_factor: float = 2.0) -> List[Dict]:
    """获取节点列表（带重试和 SSL 错误处理）"""
    print("正在请求 nodeList 接口...")
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "authorization": f"Bearer {token}",
        "accept-charset": "UTF-8",
        "Content-Type": "application/json"
    }
    
    url = f"{BASE_URL}/nodeList?platform=android"
    
    for attempt in range(max_retries + 1):
        try:
            resp = session.post(url, headers=headers, json={}, timeout=15)
            print(f"状态码: {resp.status_code}")
            
            if resp.status_code != 200:
                print(f"错误响应: {resp.text[:300]}")
                if attempt == max_retries:
                    return []
                continue
            
            data = resp.json()
            if not isinstance(data, list):
                print("响应不是列表格式")
                return []
            
            print(f"成功获取 {len(data)} 个节点")
            return data
            
        except requests.exceptions.SSLError as e:
            print(f"SSL 错误 (尝试 {attempt+1}/{max_retries+1}): {str(e)}")
            if attempt == max_retries:
                print("SSL 错误重试失败，可能是服务器 TLS 配置问题")
                return []
            
            wait_time = backoff_factor ** (attempt + 1)
            print(f"等待 {wait_time:.1f} 秒后重试...")
            time.sleep(wait_time)
            
        except Exception as e:
            print(f"获取节点列表异常 (尝试 {attempt+1}/{max_retries+1}): {str(e)}")
            if attempt == max_retries:
                return []
            
            wait_time = backoff_factor ** attempt
            print(f"等待 {wait_time:.1f} 秒后重试...")
            time.sleep(wait_time)
    
    return []


def extract_node_name(node: Dict) -> str:
    """从 nodeList 提取节点名称，用于备注"""
    if name := node.get("nameCn"):
        return name.strip()
    if name := node.get("nameEn"):
        return name.strip()
    if region := node.get("regionNameCn"):
        return f"{region.strip()} 节点"
    return f"Node-{node.get('nodeId', '未知')}"


def get_client_config(node_id: int, token: str, max_retries: int = 4, backoff_factor: float = 2.0) -> Optional[Dict]:
    url = f"{BASE_URL}/clientConfig"
    payload = {"nodeId": node_id}
    headers = {
        "User-Agent": "ktor-client",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "authorization": f"Bearer {token}",
        "accept-charset": "UTF-8",
        "Content-Type": "application/json"
    }
    
    for attempt in range(max_retries + 1):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code == 200:
                print(f"  node {node_id} 配置获取成功")
                return resp.json()
            else:
                print(f"  node {node_id} HTTP {resp.status_code} (尝试 {attempt+1}/{max_retries+1})")
                if attempt == max_retries:
                    return None
        
        except requests.exceptions.SSLError as e:
            print(f"  node {node_id} SSL 错误 (尝试 {attempt+1}/{max_retries+1}): {str(e)}")
            if attempt == max_retries:
                return None
            
            wait_time = backoff_factor ** (attempt + 1)
            print(f"  等待 {wait_time:.1f} 秒后重试...")
            time.sleep(wait_time)
            
        except RequestException as e:
            print(f"  node {node_id} 连接异常 (尝试 {attempt+1}/{max_retries+1}): {str(e)}")
            if attempt == max_retries:
                print(f"  node {node_id} 重试 {max_retries} 次后仍失败，跳过")
                return None
            
            wait_time = backoff_factor ** attempt
            print(f"  等待 {wait_time:.1f} 秒后重试...")
            time.sleep(wait_time)
    
    return None


def generate_vless_link(config: Dict, node_name: str) -> str:
    """生成 VLESS 链接（支持 reality 和普通 tls）"""
    vnext = config["settings"]["vnext"][0]
    user = vnext["users"][0]
    stream = config["streamSettings"]
    
    if stream.get("security") == "reality":
        reality = stream["realitySettings"]
        params = {
            "security": "reality",
            "encryption": user.get("encryption", "none"),
            "pbk": reality["publicKey"],
            "headerType": "none",
            "fp": reality["fingerprint"],
            "type": stream["network"],
            "sni": reality["serverName"],
            "sid": reality["shortId"]
        }
    else:
        params = {"security": stream.get("security", "none")}
    
    query = urllib.parse.urlencode(params)
    remark = urllib.parse.quote(node_name)
    link = f"vless://{user['id']}@{vnext['address']}:{vnext['port']}?{query}#{remark}"
    return link


def save_link_only(link: str):
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{link}\n")


def main():
    # 清空文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        pass
    
    print("火种VPN - 自动登录 + 提取 VLESS 链接 (GitHub Actions 版)")
    print(f"输出文件: {OUTPUT_FILE} （纯链接格式）\n")
    
    # 步骤1: 尝试登录获取新 Token
    token = login_and_get_token()
    if not token:
        print("登录失败，无法继续。请检查环境变量中的凭证")
        sys.exit(1)
    
    nodes = get_node_list(token)
    if not nodes:
        print("没有获取到任何节点，结束")
        sys.exit(1)
    
    success_count = 0
    
    for node in nodes:
        node_id = node.get("nodeId")
        if not node_id:
            continue
        
        node_name = extract_node_name(node)
        
        config = get_client_config(node_id, token)
        if not config:
            continue
        
        protocol = config.get("protocol", "").lower()
        
        # 仅处理 VLESS 协议，跳过其他（包括 vmess）
        if protocol != "vless":
            continue
        
        try:
            link = generate_vless_link(config, node_name)
            save_link_only(link)
            success_count += 1
            print(f"已保存 VLESS 节点 {node_id} ({node_name}) → {link[:60]}...")
        
        except Exception as e:
            print(f"生成 VLESS 链接失败 (node {node_id}): {str(e)}")
    
    print(f"\n完成！共保存 {success_count} 条 VLESS 链接")
    print(f"文件路径: {OUTPUT_FILE}")
    
    # 如果一条链接都没生成，视为失败
    if success_count == 0:
        print("警告：未生成任何有效 VLESS 链接")
        sys.exit(1)


if __name__ == "__main__":
    main()
