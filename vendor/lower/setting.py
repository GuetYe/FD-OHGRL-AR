#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/14 20:52
@File:setting.py
@Desc:参数设计
"""
wired = True

# 权重文件夹路径
weight_file_path = './ryu/pickle/'   # 源代码network_shortest_path.py中写死
"""
后面有两层文件，均需要进行时间优先提取
"""

# 路径文件夹路径
path_file_path = './ryu/savepath/path_table.json'  # 源代码network_shortest_path.py中写死
action_file_path = './ryu/save_action/action.json' # 源代码network_shortest_path.py中写死


# 路径特征名字
path_feature_names = ['free_bw', 'delay', 'loss', 'forward_queue_pkts', 'distance']
# MIN：最小性特征，PROD：乘性特征， FSUM:加性特征
feature_types = ['MIN', 'FSUM', 'PROD', 'FSUM', 'FSUM']
assert len(path_feature_names) == len(feature_types), f'特征名称:{path_feature_names}数目和特征类型{feature_types}数目不一致,请检查。'

# 目标属性和表征属性
label_addr = 'd2d_time'
# feature_addr = ['free_bw', 'delay', 'loss', 'forward_queue_pkts', 'distance']
feature_addr = ['free_bw', 'delay', 'loss', 'forward_queue_pkts']
# feature_addr = ['free_bw', 'loss', 'forward_queue_pkts']

# 路径属性路径
path_feature_file_path = './ryu/path_features/'
total_path_features_file_path = './ryu/total_path_features/'
total_path_features_file_name = 'total_path_features_backup0.json'

# 在服务器上的路径
total_path_features_file_server_path = '../../data/path_data/'   # 路径特征
model_save_path = '../../data/models/'   # 模型存储路径


# 路径特征请求指令
request_path_feature_ins = b'Request path_features'

# 初始化源节点和目的节点
src_node_list = [3]
dst_node_list = [15, 16, 17, 18]
# src_node_list = [1]
# dst_node_list = [5, 6]

server_ip_port = {}
choice_pro = {}
for src in src_node_list:
    for dst in dst_node_list:
        if wired:
            server_ip_port.update({f'({src}, {dst})': (f'10.0.0.{dst}', 5001)})
            choice_pro.update({f'({src}, {dst})': 1 / (len(src_node_list) * len(dst_node_list))})
        else:
            server_ip_port.update({f'({src}, {dst})': (f'192.168.0.{dst}', 5001)})
            choice_pro.update({f'({src}, {dst})':1/(len(src_node_list)*len(dst_node_list))})

last_choice_pro = None  # 用于记录之前的选择动作
last_control_tag = None  # 用于记录之前的动作状态

# print(server_ip_port)
# print(choice_pro)
# server_ip_port = {
#     '(1, 15)': ('192.168.0.15', 5001),
#     '(1, 16)': ('192.168.0.16', 5001),
#     '(1, 17)': ('192.168.0.17', 5001),
#     '(1, 18)': ('192.168.0.18', 5001),
# }
# choice_pro = {
#     '(1, 15)': 0.25,
#     '(1, 16)': 0.25,
#     '(1, 17)': 0.25,
#     '(1, 18)': 0.25,
# }

# 目标节点概率路径
dst_node_pro_file_path = './ryu/dst_node_pro_file/'
dst_node_pro_file_name = 'dst_node_pro.json'


# 数据服务器ip和端口
# data_server_ip = "10.0.6.101"  # 注意在不同的网络下要进行修改，否者无反应
# # data_server_ip = '10.0.6.101'
data_server_ip = '127.0.0.1'
data_server_port = 5000

# 发送数据包的多少
packet_nums = 100000
lambda_value = 1.5   # 假设每秒到达1个数据流
# data_size_mean = 2000  # 数据包大小的均值(字节)
# data_size_std = 200   # 数据包大小的标准差(字节)
# data_size_mean = 1000
# data_size_std = 100
data_size_mean = 1000
data_size_std = 100


# 滑动窗口
window_size = 50

# 端到端时延时延评价间隔
evaluate_interval = 200

# 结果路径
result_path = './result/'
tensorboard_log_path = result_path + 'tensorboard/'
tensorboard_run_name = None

# 背景流信息
background_data_size_mean = 20  # 背景流数据流的大小均值
background_data_size_std = 2  # 背景流数据流的大小标准差
background_data_lambda_value = 10  # 背景流间隔时间
background_data_rate = 0  # 背景流概率

# 日志管理
import logging
print_level = logging.INFO
file_level = logging.DEBUG

max_retries = 50  # 通信尝试次数

# 数据包记录
successful_sent = 0
successful_gen = 0

# 传输数据额线程数量
thread_num = 20
