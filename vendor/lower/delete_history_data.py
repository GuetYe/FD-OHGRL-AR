#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/5/3 19:26
@File:delete_data.py
@Desc:****************
"""
# 清空历史模型数据
import shutil
import os
import setting
import sys

def delete_data(path_list):
    for path in path_list:
        new_model_path = os.path.normpath(path)
        if os.path.exists(new_model_path):
            shutil.rmtree(new_model_path)
            print(f"Deleted: {new_model_path}")
            os.makedirs(new_model_path)
            print(f"Recreated folder: {new_model_path}")
        else:
            print(f"Path does not exist: {new_model_path}")
            os.makedirs(new_model_path)
            print(f"Recreated folder: {new_model_path}")

if __name__ == '__main__':
    path_list = ['./ryu/savepath/', './ryu/save_action/',
                 './ryu/dst_node_pro_file/','./result/figure/','./result/data/']
    total_path_list = ['./ryu/path_features/']
    if len(sys.argv)>1:
        for path in total_path_list:
            path_list.append(path)
    delete_data(path_list)


