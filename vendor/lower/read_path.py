#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/3/14 16:35
@File:read_path.py
@Desc:读取路径
"""
import json
import time

# 保存路径"./savepath/path_table.json"
def safe_read_path(file_path,max_retries=5,retry_delay=0.5):
    for _ in range(max_retries):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError,json.JSONDecodeError):
            # 文件不存在或内容不完整时重试
            time.sleep(retry_delay)
        raise Exception(f'读取失败：{file_path}不存在或数据不支持读取')


if __name__ == '__main__':
    while True:
        try:
            data = safe_read_path("./ryu/savepath/path_table.json")
            print('读取成功：',data)
        except Exception as e:
            print(f'错误：{e}')
        time.sleep(0.5)



