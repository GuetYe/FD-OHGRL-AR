#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/2/19 19:37
@File:show_ip_addr.py
@Desc:****************
"""
import netifaces
# 获取所有网卡接口
interfaces = netifaces.interfaces()
# 遍历每个接口
for interface in interfaces:
    # 获取接口的地址信息
    addresses = netifaces.ifaddresses(interface)
    # 查找 IPv4 地址
    if netifaces.AF_INET in addresses:
        for link in addresses[netifaces.AF_INET]:
            print(f"接口: {interface}, IP 地址: {link['addr']}")