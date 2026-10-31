#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/18 21:04
@File:get_path_feature.py
@Desc:****************
"""
import setting
import os
import json
import copy
import pandas as pd

def get_path_feature():
    """
    从文件读取数据，并将它们打包成字典，字典的形式发送给用户
    :return:
    """
    path_feature_path = setting.path_feature_file_path  # 数据保证的路径
    # 遍历目录下的文件
    files_list = os.scandir(path_feature_path)
    count = 0  # 变量计数器

    for file_path in files_list:
        if file_path.is_file():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    path_feature_dict = json.load(f)
                    # print(type(path_feature_dict))
                    # print(path_feature_dict.keys())
            except Exception as e:
                print(f'文件打卡错误：{e}')
        if count == 0:
            path_features_dict = copy.deepcopy(path_feature_dict)
            # print('---->',path_features_dict)
            count = 1
        else:
            try:
                # print('=====>',path_features_dict)
                path_features_dict = {
                    key: [*path_features_dict[key], *path_feature_dict[key]] for key in path_features_dict.keys()
                }
            except Exception as e:
                print(e)
            count += 1
        print(f'数据采集量：{count}')

    total_path_features_file_path = setting.total_path_features_file_path
    total_path_features_file_name = setting.total_path_features_file_name
    path_features_file_path = total_path_features_file_path+total_path_features_file_name
    with open(path_features_file_path, 'w', encoding='utf-8') as f:
        json.dump(path_features_dict, f, ensure_ascii=False, indent=4)
    return path_features_dict

if __name__ == '__main__':
    path_features_dict = get_path_feature()
    path_features_dataframe = pd.DataFrame(path_features_dict)  # 字典转化为 -- dataframe
    print(path_features_dataframe)


