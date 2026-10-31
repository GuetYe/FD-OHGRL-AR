#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/2/19 16:39
@File:anycast_tcp_client.py
@Desc:****************
"""
import os.path
import random
import socket
import time
from concurrent.futures import ThreadPoolExecutor
import sys
import utils
from multiprocessing import Pool
import setting
import pandas as pd
import copy
import json
import requests
from utils import PacketGenerator,IncrementalMovingAverage,Evaluate,log_management,generate_large_packet,call_back,server_choice,send_large_packet
import numpy as np
import asyncio
from functools import partial



if __name__ == '__main__':
    file_name = os.path.basename(__file__).split('.')[0]
    logger = log_management(file_name,setting.print_level,setting.file_level)
    # 定义服务端地址和端口
    prefix = 'SAC'
    now_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    servers = setting.server_ip_port
    # 获取参数，确定是随机性还是确定性发流
    # tag = 'unicast'(15),'sto_anycast'(随机任播)

    # print(servers)
    choice_server = list(servers.values())[2]  # 用于设置单播目标服务器
    # print(choice_server)
    if len(sys.argv)==1:
        tag = 'unicast'
    else:
        tag = sys.argv[1]
    # 生成1000 MB的数据包
    # 模拟发送50个数据包的情况
    pool = ThreadPoolExecutor(setting.thread_num)  # 不传参的话，默认开设的线程数量，是当前cpu的个数乘以5
    # tag_num = 0
    # path_feature = None
    generater = PacketGenerator(data_size_mean=setting.data_size_mean,data_size_std=setting.data_size_std,
                                lambda_value=setting.lambda_value)

    ma = IncrementalMovingAverage(setting.window_size)
    ma_value_list = []  # 用于保存滑动平均的端到端时延
    control_tag_list = []  # 用于保存当前控制的状态，采样状态为“S”，训练过程中“T”，训练结束之后为“C”
    servers_list = []  # 目的节点选择
    time_list = []  # 发送数据的时间节点
    save_figure_path = setting.result_path + 'figure/'
    save_data_path = setting.result_path + 'data/'

    d2d_time_eva = Evaluate(save_figure_path=save_figure_path,save_data_path=save_data_path)
    for send_packet_num in range(setting.packet_nums):
        if random.random() < setting.background_data_rate:
            packet_size, inter_arrival_time = generater.generate_background_flow()
            flow_type = 'B'
        else:
            packet_size, inter_arrival_time = generater.generate_packet()
            flow_type = 'T'
        packet = generate_large_packet(packet_size)
        setting.successful_gen += 1
        logger.info(f'成功生成数据包个数{setting.successful_gen}') # 1s 4个数据流
        time.sleep(inter_arrival_time)
        # 向多个服务器发送数据包
        if tag == 'unicast' and flow_type=='B':
            server, _ = server_choice(servers,is_interaction=False)
            control_tag = 'S'
        elif tag == 'unicast' and flow_type == 'T':
            server = choice_server
            control_tag = 'S'
        elif flow_type == 'B':
            server, _ = server_choice(servers, is_interaction=False)
            control_tag = 'S'
        else:
            server,control_tag = server_choice(servers,is_interaction=True)
            # pro_dict = setting.choice_pro  # 改成动作
            # # 保存概率-方便记录
            # dst_pro_file_name = setting.dst_node_pro_file_path+setting.dst_node_pro_file_name
            # with open(dst_pro_file_name, 'w', encoding='utf-8') as f:
            #     json.dump(pro_dict, f, ensure_ascii=False, indent=4)
            # choice_key = weighted_random(pro_dict)
            # server = servers[choice_key]
            # # server = random.choices(servers,k=1)[0]
        if flow_type == 'T':
            control_tag_list.append(control_tag)
            servers_list.append(server)
            time_list.append(time.time())
        ip, port = server
        pool.submit(send_large_packet,ip,port,packet,flow_type).add_done_callback(partial(call_back,value_list=ma_value_list,method=ma))  # 通过回调机制获取结果

        if send_packet_num % setting.evaluate_interval==0 and send_packet_num>0:
            eva_value_list = copy.deepcopy(ma_value_list)
            eva_count_list = [i + 1 for i in range(len(eva_value_list))]
            control_tag_list1 = [control_tag_list[i] for i in range(len(eva_value_list))]
            servers_list1 = [servers_list[i] for i in range(len(eva_value_list))]
            time_list1 = [time_list[i] for i in range(len(eva_value_list))]

            # 异步提交绘图和数据保存的任务
            loop = asyncio.new_event_loop()
            loop.run_until_complete(d2d_time_eva.data_display(eva_count_list=eva_count_list,
                                    eva_value_list=eva_value_list,control_tag_list=control_tag_list1,
                                    servers_list=servers_list1,time_list=time_list1,prefix=prefix,tag_name=str(send_packet_num),now_time=now_time))

    # 执行代码： python anycast_tcp_client.py --sto_anycast
    # 执行代码： nohup python3 -u anycast_tcp_client.py --sto_anycast >> ./main7-8-1_output.log 2>&1