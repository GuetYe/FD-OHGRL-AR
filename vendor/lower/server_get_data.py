#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/4/6 10:37
@File:server_get_data.py
@Desc:服务器获取数据
"""
import requests
import setting
import time

server_ip = setting.data_server_ip  # 数据服务器ip
server_port = setting.data_server_port  # 数据服务器端口

if __name__ == '__main__':
    num = 1000
    for i in range(num):
        response = requests.get(f'http://{server_ip}:{server_port}/api/state')
        print("API Response:", response.json())
        time.sleep(10)

