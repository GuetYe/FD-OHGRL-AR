#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
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
from utils import PacketGenerator,IncrementalMovingAverage,Evaluate,log_management
import numpy as np
import asyncio
from functools import partial



# 生成相应大小的数据包
def generate_large_packet(size_kb):
    """
    模拟数据包的生成
    :param size_kb: 模拟数据包的大小,单位为字节
    :return:
    """
    return b'x'*(int(size_kb*1000))+b'\n'

# 向服务器端发送数据包
def send_large_packet(ip,port,packet,flow_type):
    """
    模拟数据包的发送
    :param ip: 服务端的ip
    :param port: 服务端的端口
    :param packet: 需要发送的数据包
    :return: 返回数据包端到端时延.数据包的大小
    """
    # d2d_time = None  # 默认是None
    try:
        # 创建socket
        client_socket = socket.socket()
        client_socket.connect((ip,port))
        print(f'已连接到服务端{ip}：{port}')
        # 发送数据包
        send_time = time.time()
        # 发送数据包前利用进程获取当前选择的路径属性
        client_socket.send(packet)
        path_features_dict = get_path_features(ip)
        # print(f'已向{ip}：{port}发送{len(packet)}字节的数据包')
        # 接收相应
        massage = client_socket.recv(1024).decode('utf-8')
        # print(f"从{ip}：{port}接收到响应{massage}")
        _, receive_time = massage.split(':',1)
        receive_time = float(receive_time)
        d2d_time = receive_time - send_time
        # print(f'数据的端到端传输时延为{d2d_time:6f}秒')
        # 关闭连接
        client_socket.close()
        # print(f'与{ip}：{port}的连接已关闭')
        packet_size = len(packet)

    except Exception as e:
        logger.info(f'与服务端{ip}：{port}通信时发生错误：{e}，当前时间：{time.time()}')
        time.sleep(1)  # 报错后等待一下，避免系统杀死进程
        d2d_time, packet_size, path_features_dict, _ = send_large_packet(ip,port,packet,flow_type)  # 重新传输，避免出现重传错误

    return d2d_time, packet_size, path_features_dict, flow_type

def get_path_features(dst_ip):
    """
    获取path对应的
    :param : 服务器ip -- 用于获取dst
    :return:
    """
    # 1. 获取src
    # print('执行获取path特征')
    src_ip = utils.get_ip_addr('sta') if utils.get_ip_addr('sta') else utils.get_ip_addr('h')
    src = int(src_ip.split('.')[-1])  # 获取源节点编号
    dst = int(dst_ip.split('.')[-1])  # 获取目的节点编号
    weight_file_path_prefix = setting.weight_file_path
    weight_file_path = utils.read_last_weight_file(weight_file_path_prefix)
    path_file_path = setting.path_file_path
    path, weight_graph = utils.get_path_and_weight_graph(src=src, dst=dst, path_file_path=path_file_path,
                                                   weight_file_path=weight_file_path)  # 获取对应的权重图
    path_features_dict = utils.compute_path_features(path,weight_graph)   # 获取特征字典
    return path_features_dict

def call_back(res,value_list,method):
    """
    对线程输出的结果进行处理
    :param res: 包含端到端时延和数据包长度
    :return: 将结果保存
    """
    d2d_time, packet_size, path_features_dict, flow_type = res.result()
    if flow_type == 'B':
        pass
    else:
        path_features_dict['d2d_time'] = [d2d_time,]
        path_features_dict['packet_size'] = [packet_size,]
        now_time = time.strftime("%Y-%m-%d-%H-%M-%S",time.localtime())
        json_file_name = setting.path_feature_file_path + now_time + '.json'
        with open(json_file_name, 'w', encoding='utf-8') as f:
            json.dump(path_features_dict,f,ensure_ascii=False,indent=4)
        try:
            value_list.append(method.add(d2d_time))
        except Exception as e:
            print(f'Exception in thread:{e}')


def weighted_random(pro_dict):
    """
    根据概率权重选择目标主机
    :param pro_dict: 权重字典
    :return:
    """
    rand_val = random.random()   # 生成0-1之间的随机数
    cumulative = 0
    if isinstance(pro_dict,dict):
        for key, weight in pro_dict.items():
            cumulative += weight
            if rand_val <= cumulative:
                return key
        return list(pro_dict.keys())[-1]  # 处理浮点进度问题
    elif isinstance(pro_dict,list):
        keys = list(setting.choice_pro.keys())
        for key, weight in zip(keys, pro_dict[0]):
            cumulative += weight
            if rand_val <= cumulative:
                return key
        return keys[-1]
    else:
        print(f"[action]出现了不支持的数据类型{pro_dict}")
        return setting.choice_pro.keys()[-1]

def server_choice(servers,is_interaction=True):
    """
    从数据服务器获取动作进行决策
    :param is_interaction 是否与数据服务器进行交互
    :return:
    """
    if is_interaction:  # 若与数据服务器进行交互，需要读取action文件中的数据,为了和下层动作一致，需要通过文件记录
        # 读取动作文件
        action_file = setting.action_file_path
        high_action_pro_list = setting.choice_pro
        try:
            with open(action_file, 'r', encoding='utf-8') as f:
                action_dict = json.load(f)
            high_action_pro_list = action_dict['high_act_list']
            control_tag = action_dict['control_tag']
            setting.last_choice_pro = high_action_pro_list
            setting.last_control_tag = control_tag
        except Exception as e:
            print('打开文件报错：', e)
            if setting.last_choice_pro:
                high_action_pro_list = setting.last_choice_pro
                control_tag = setting.last_control_tag
                print(f'控制以开始，动作文件打开有问题，使用上次的概率{high_action_pro_list}')
            else:
                print(f'训练动作还未下发，先使用默认的概率{high_action_pro_list}')
                control_tag = 'S'

        # 保存目的概率字典，方便记录发送
        dst_pro_file_name = setting.dst_node_pro_file_path + setting.dst_node_pro_file_name
        pro_dict = list_to_dict(setting.choice_pro.keys(),high_action_pro_list)
        # 按概率选择服务器
        choice_key = weighted_random(high_action_pro_list)
        server = servers[choice_key]
        print(f'决策概率{high_action_pro_list},选择目标为{server}')
        up_action_dict = {'pro_dict':pro_dict,'choice_key':choice_key}
        with open(dst_pro_file_name, 'w', encoding='utf-8') as f:
             json.dump(up_action_dict, f, ensure_ascii=False, indent=4)
        return server,control_tag
    else:
        pro_dict = setting.choice_pro  # 改成动作
        # 保存概率-方便记录
        # dst_pro_file_name = setting.dst_node_pro_file_path + setting.dst_node_pro_file_name
        # with open(dst_pro_file_name, 'w', encoding='utf-8') as f:
        #     json.dump(pro_dict, f, ensure_ascii=False, indent=4)
        choice_key = weighted_random(pro_dict)
        server = servers[choice_key]
        control_tag = 'S'
        return server,control_tag

def list_to_dict(keys_list,value_list):
    """
    列表转字典
    :param keys_list:
    :param value_list:
    :return:
    """
    new_dict = {}
    if isinstance(value_list,list):
        new_value_list = np.array(value_list).flatten().tolist()
        for key,value in zip(keys_list,new_value_list):
            new_dict[key] = value
        return new_dict
    elif isinstance(value_list,dict):
        return value_list
    else:
        print(f'不支持的数据类型{value_list}，请检查')
        raise TypeError(f'不支持的数据类型{value_list}，请检查')



if __name__ == '__main__':
    file_name = os.path.basename(__file__).split('.')[0]
    logger = log_management(file_name,setting.print_level,setting.file_level)
    # 定义服务端地址和端口
    prefix = 'DDPG'
    now_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    servers = setting.server_ip_port
    # 获取参数，确定是随机性还是确定性发流
    # tag = 'unicast'(15),'sto_anycast'(随机任播)

    # print(servers)
    choice_server = list(servers.values())[0]  # 用于设置单播目标服务器
    # print(choice_server)
    if len(sys.argv)==1:
        tag = 'unicast'
    else:
        tag = sys.argv[1]
    # 生成1000 MB的数据包
    # 模拟发送50个数据包的情况
    pool = ThreadPoolExecutor(10)  # 不传参的话，默认开设的线程数量，是当前cpu的个数乘以5
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