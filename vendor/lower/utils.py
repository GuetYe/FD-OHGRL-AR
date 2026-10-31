#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/14 16:47
@File:utils.py
@Desc:用于存放一些工具
"""
import json
import os
import time
import setting
import networkx as nx
import pandas as pd
import netifaces
from collections import deque
import matplotlib.pyplot as plt
import requests
import logging
import socket
import random
import threading
import atexit

# 日志管理系统
def log_management(file_name,print_level=logging.INFO,file_level=logging.DEBUG):
    """
    输出日志
    :param file_name: 调用日志的文件名
    :param print_level: 打印等级
    :param file_level: 文件输出等级
    :return:
    """
    formatted_time = time.strftime("%Y-%m-%d_%H-%M-%S",time.localtime())
    log_format = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",datefmt="%m/%d/%Y %H:%M:%S %p")
    # 创建logger
    logger = logging.getLogger('run_logger')  # 创建打印日志--运行时打印的日志
    logger.setLevel(logging.DEBUG)  # 选择打印日志的等级
    """
    DEBUG < INFO < WARNING < ERROR < CRITICAL
    DEBUG: 用于测试打印(测试信息-不要紧的消息、所有信息)
    INFO：相对有用的提示(用于操作提示)
    WARNING：必要的信息(用于结果显示)
    ERROR：更高级的设置
    CRITICAL：更高级的设置
    """
    # 创建文件日志 file_logger
    log_dir = './logging'
    os.makedirs(log_dir, exist_ok=True)
    file_logger_handler = logging.FileHandler(f'./logging/{file_name}_run_logger_{formatted_time}.log')
    file_logger_handler.setLevel(file_level)

    # 为文件日志设置格式
    file_logger_handler.setFormatter(log_format)

    # 添加文件日志
    logger.addHandler(file_logger_handler)

    # 添加打印日志
    console_handler = logging.StreamHandler()
    console_handler.setLevel(print_level)
    # 设置打印格式
    console_handler.setFormatter(log_format)

    # 添加打印日志
    logger.addHandler(console_handler)

    # 添加第一条日志
    logger.critical(f'[{file_name}.py]_run_logger_{formatted_time}')  # 以最高级别打印标题

    return logger

file_name = os.path.basename(__file__).split('.')[0]
logger = log_management(file_name,setting.print_level,setting.file_level)

# 读取路径
# 保存路径"./savepath/path_table.json"
def safe_read_path(file_path,max_retries=50,retry_delay=0.5):
    for _ in range(max_retries):
        try:
            with open(file_path,'r',encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError,json.JSONDecodeError):
            # 文件不存在或内容不完整时重试
            time.sleep(retry_delay)
    else:
        print(f'读取失败：{file_path}不存在或数据不支持读取')


# 读取最新权重文件
def read_last_weight_file(path):
    """
    读取最新的权重文件，两层文件
    :param path:
    :return:
    """
    try:
        # 遍历目录
        entries = os.scandir(path)
        if not entries:
            return None
        # 用修改时间作为key，找到最大值的文件
        lastest = max(entries,key=lambda x: x.stat().st_mtime)
        if not lastest.is_file():
            return read_last_weight_file(lastest.path)
        else:
            return lastest.path

    except FileNotFoundError:
        return None  # 处理目录不存在的情况


def get_path_and_weight_graph(src,dst,path_file_path,weight_file_path):
    """
    根据源和目标获取路径和相关的特征
    :param src:
    :param dst:
    :param path_file_path: 路径文件夹路径
    :param weight_file_path: 权重文件夹路径
    :return: 路径和权重图
    """
    path_key = str((src,dst))  # 路径键值
    # response = requests.get(
    #     f'http://{setting.data_server_ip}:5001/api/path_table',
    #     timeout=5
    # )
    # path = response.json()[path_key]  # 读取对应的路径
    path_tables = safe_read_path(file_path=path_file_path)  # 读取path文件
    path = path_tables[path_key]  # 读取对应的路径
    weight_graph = nx.read_gpickle(weight_file_path)  # 读取权重图
    return path,weight_graph

def compute_path_features(path,weight_graph):
    """
    计算路径的权重特征
    :param path:
    :param weight_graph:
    :return: 特征的dataframe
    """
    features_names = setting.path_feature_names
    feature_types = setting.feature_types
    path_features = {}
    for feature_name,feature_type in zip(features_names,feature_types):
        type_tag = 0    # 用于标识第1次调用
        feature_value = 0
        for u,v in zip(path[0:-2],path[1:-1]):  # 分解路径为链路
            # MIN：最小性特征，PROD：乘性特征， FSUM:加性特征
            if feature_type == 'MIN':  # 最小型特征的处理
                if type_tag == 0:
                    feature_value = weight_graph[u][v][feature_name]
                    type_tag = 1
                else:
                    feature_value = min(feature_value,weight_graph[u][v][feature_name])
            elif feature_type == 'PROD':  # 加性特征的处理
                if type_tag == 0:
                    feature_value = 1-weight_graph[u][v][feature_name]
                    type_tag = 1
                else:
                    feature_value *= 1-weight_graph[u][v][feature_name]
                    # print('loss',feature_value)
            elif feature_type == 'FSUM':
                if type_tag == 0:
                    feature_value = weight_graph[u][v][feature_name]
                    type_tag = 1
                else:
                    feature_value += weight_graph[u][v][feature_name]

        if feature_type == 'PROD':
            feature_value = 1 - feature_value
        path_features[feature_name] = [feature_value,]

    # print(path_features)
    # path_feature_dict = path_features
    return path_features

# 获取ip地址
# 获取所有网卡接口
def get_ip_addr(feature):
    """
    获取含有feature的ip账号
    :param feature:
    :return:
    """
    interfaces = netifaces.interfaces()
    print(interfaces)
    # 遍历每个接口
    for interface in interfaces:
        # 获取接口的地址信息
        addresses = netifaces.ifaddresses(interface)
        # 查找 IPv4 地址
        if netifaces.AF_INET in addresses:
            for link in addresses[netifaces.AF_INET]:
                if feature in interface:
                    return link['addr']


import numpy as np

class PacketGenerator:
    def __init__(self, data_size_mean, data_size_std, lambda_value):
        """
        初始化数据包生成器。

        参数：
        - data_size_mean: 数据包大小的均值（正态分布的均值）。
        - data_size_std: 数据包大小的标准差（正态分布的标准差）。
        - lambda_value: 指数分布的速率参数（单位时间内事件发生的平均次数）。
        """
        self.data_size_mean = data_size_mean
        self.data_size_std = data_size_std
        self.lambda_value = lambda_value

    def generate_packet(self):
        """
        生成一个数据包及其时间间隔。

        返回：
        - packet_size: 数据包大小，服从正态分布。
        - inter_arrival_time: 数据包之间的时间间隔，服从指数分布。
        """
        # 生成数据包大小（正态分布）
        packet_size = np.random.normal(loc=self.data_size_mean, scale=self.data_size_std)

        # 处理异常值（数据包大小不能为负数）
        packet_size = max(0, packet_size)

        # 生成时间间隔（指数分布）
        inter_arrival_time = np.random.exponential(scale=1 / self.lambda_value)

        return packet_size, inter_arrival_time

    def generate_background_flow(self):
        """
        生成背景流
        :return:
        """
        # 生成数据包大小（正态分布）
        packet_size = np.random.normal(loc=setting.background_data_size_mean, scale=setting.background_data_size_std)

        # 处理异常值（数据包大小不能为负数）
        packet_size = max(0, packet_size)

        # 生成时间间隔（指数分布）
        inter_arrival_time = np.random.exponential(scale=1 / setting.background_data_lambda_value)

        return packet_size, inter_arrival_time

# 增量式计算均值
class IncrementalMovingAverage:
    def __init__(self,window_size):
        self.window_size = window_size
        self.window_deque = deque(maxlen=window_size)  # 用于保持数据
        self.value_sum = 0.0  # 累计和
        self.value_num = 0  # 用于处理未达到窗口的数据

    def madd(self, value):
        self.value_num += 1
        if len(self.window_deque) == self.window_size:
            self.value_sum -= self.window_deque[0]  # 减去最久值
        self.window_deque.append(value)
        self.value_sum += value
        if len(self.window_deque) == self.window_size:
            return self.value_sum/self.window_size
        else:
            return self.value_sum/self.value_num

    def add(self, value):
        return value

class Evaluate:
    def __init__(self, save_figure_path='../result/figure/',save_data_path='../result/data/'):
        """
        评价类
        :param save_figure_path: 保存图片的路径
        :param save_data_path: 保存数据的路径
        """
        self.save_figure_path = save_figure_path
        self.save_data_path = save_data_path

    async def data_display(self, eva_count_list,eva_value_list,control_tag_list,servers_list,
                           time_list, prefix,tag_name,now_time):
        """
        显示评价结果
        :param eva_count_list: 评价对应的episode列表
        :param eva_value_list: 评价的值列表，上下层不一样，上层是值列表，下层是字典列表，分两种情况进行处理
        :param tag_name: 标识性名字，用于区分不同的绘图，file_name+次数
        :return: 保存三个数据和图片
        """
        try:
            if isinstance(eva_value_list, list):
                # 将数据转化为pandas
                data = {
                    'eva_count_list': eva_count_list,
                    'eva_value_list': eva_value_list,
                    'control_tag_list': control_tag_list,
                    'servers_list': servers_list,
                    'time_list': time_list
                }
                df = pd.DataFrame(data)

                title_name1 = 'eva_value_data'
                title_name2 = 'eva_value_over_count_list'
                plt.figure()
                df.plot(x='eva_count_list', y='eva_value_list', title=title_name2)
                plt.savefig(f'{self.save_figure_path}{prefix}_{tag_name}_{title_name1}_{now_time}')

                df.to_csv(f'{self.save_data_path}{prefix}_{tag_name}_{title_name1}_{now_time}.csv',index=False,encoding='utf-8-sig')   # 支持Excel打开

            else:
                print(f'[Evalue]暂时不支持这种数据{eva_value_list}，请检查。。。')
        except Exception as e:
            print('绘图错误：', e)


# 生成相应大小的数据包
def generate_large_packet(size_kb):
    """
    模拟数据包的生成
    :param size_kb: 模拟数据包的大小,单位为字节
    :return:
    """
    return b'x' * (int(size_kb * 1000)) + b'\n'


# 向服务器端发送数据包
def send_large_packet(ip, port, packet, flow_type):
    """
    模拟数据包的发送
    :param ip: 服务端的ip
    :param port: 服务端的端口
    :param packet: 需要发送的数据包
    :return: 返回数据包端到端时延.数据包的大小
    """
    # d2d_time = None  # 默认是None
    attempt = 0
    while attempt < setting.max_retries:
        try:
            # 创建socket
            client_socket = socket.socket()
            client_socket.connect((ip, port))
            print(f'已连接到服务端{ip}：{port}')
            # 发送数据包
            send_time = time.time()
            # 发送数据包前利用进程获取当前选择的路径属性
            client_socket.send(packet)
            path_features_dict = get_path_features(ip)
            setting.successful_sent += 1
            logger.info(f'成功发送数据包个数{setting.successful_sent}')

            # print(f'已向{ip}：{port}发送{len(packet)}字节的数据包')
            # 接收相应
            massage = client_socket.recv(1024).decode('utf-8')
            # print(f"从{ip}：{port}接收到响应{massage}")
            _, receive_time = massage.split(':', 1)
            receive_time = float(receive_time)
            d2d_time = receive_time - send_time
            # print(f'数据的端到端传输时延为{d2d_time:6f}秒')
            # 关闭连接
            client_socket.close()
            # print(f'与{ip}：{port}的连接已关闭')
            packet_size = len(packet)
            return d2d_time, packet_size, path_features_dict, flow_type

        except Exception as e:
            attempt += 1
            logger.info(f'与服务端{ip}：{port}通信时发生错误：{e}，当前时间：{time.time()}')
            time.sleep(1)  # 报错后等待一下，避免系统杀死进程

    # 超过重试次数仍然失败，返回特殊值或抛异常
    logger.info(f'与服务端{ip}：{port}通信失败超过{setting.max_retries}次，放弃发送。')
    return None, 0, None, flow_type


def get_path_features(dst_ip):
    """
    获取path对应的
    :param : 服务器ip -- 用于获取dst
    :return:
    """
    # 1. 获取src
    # print('执行获取path特征')
    src_ip = get_ip_addr('sta') if get_ip_addr('sta') else get_ip_addr('h')
    src = int(src_ip.split('.')[-1])  # 获取源节点编号
    dst = int(dst_ip.split('.')[-1])  # 获取目的节点编号
    weight_file_path_prefix = setting.weight_file_path
    weight_file_path = read_last_weight_file(weight_file_path_prefix)
    path_file_path = setting.path_file_path
    path, weight_graph = get_path_and_weight_graph(src=src, dst=dst, path_file_path=path_file_path,
                                                         weight_file_path=weight_file_path)  # 获取对应的权重图
    path_features_dict = compute_path_features(path, weight_graph)  # 获取特征字典
    return path_features_dict


_tensorboard_lock = threading.Lock()
_tensorboard_writer = None
_tensorboard_count = 0
_tensorboard_mean = 0.0
_tensorboard_m2 = 0.0
_tensorboard_window = deque(maxlen=50)


def _get_tensorboard_writer():
    global _tensorboard_writer
    if _tensorboard_writer is None:
        from tensorboardX import SummaryWriter
        run_name = setting.tensorboard_run_name or (
            time.strftime('%Y%m%d-%H%M%S', time.localtime()) + f'-pid{os.getpid()}')
        _tensorboard_writer = SummaryWriter(
            logdir=os.path.join(setting.tensorboard_log_path, run_name), flush_secs=10)
        atexit.register(close_tensorboard)
    return _tensorboard_writer


def close_tensorboard():
    global _tensorboard_writer
    with _tensorboard_lock:
        if _tensorboard_writer is not None:
            _tensorboard_writer.flush()
            _tensorboard_writer.close()
            _tensorboard_writer = None


def call_back(res, value_list, method):
    """
    对线程输出的结果进行处理
    :param res: 包含端到端时延和数据包长度
    :return: 将结果保存
    """
    d2d_time, packet_size, path_features_dict, flow_type = res.result()
    if flow_type == 'B':
        pass
    else:
        path_features_dict['d2d_time'] = [d2d_time, ]
        path_features_dict['packet_size'] = [packet_size, ]
        now_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
        os.makedirs(setting.path_feature_file_path, exist_ok=True)
        json_file_name = setting.path_feature_file_path + now_time + '.json'
        with open(json_file_name, 'w', encoding='utf-8') as f:
            json.dump(path_features_dict, f, ensure_ascii=False, indent=4)
        try:
            global _tensorboard_count, _tensorboard_mean, _tensorboard_m2
            with _tensorboard_lock:
                value_list.append(method.add(d2d_time))
                _tensorboard_count += 1
                delta = d2d_time - _tensorboard_mean
                _tensorboard_mean += delta / _tensorboard_count
                _tensorboard_m2 += delta * (d2d_time - _tensorboard_mean)
                _tensorboard_window.append(d2d_time)
                writer = _get_tensorboard_writer()
                step = _tensorboard_count
                window = list(_tensorboard_window)
                writer.add_scalar('FCT/flow_seconds', d2d_time, step)
                writer.add_scalar('FCT/cumulative_mean_seconds', _tensorboard_mean, step)
                writer.add_scalar('FCT/cumulative_std_seconds',
                                  (_tensorboard_m2 / (_tensorboard_count - 1)) ** 0.5 if _tensorboard_count > 1 else 0.0,
                                  step)
                writer.add_scalar('FCT/window50_mean_seconds', float(np.mean(window)), step)
                writer.add_scalar('FCT/window50_std_seconds', float(np.std(window)), step)
                writer.add_scalar('FCT/window50_p95_seconds', float(np.percentile(window, 95)), step)
                writer.add_scalar('Traffic/packet_size_bytes', packet_size, step)
        except Exception as e:
            print(f'Exception recording flow metrics: {e}')


def weighted_random(pro_dict):
    """
    根据概率权重选择目标主机
    :param pro_dict: 权重字典
    :return:
    """
    rand_val = random.random()  # 生成0-1之间的随机数
    cumulative = 0
    if isinstance(pro_dict, dict):
        for key, weight in pro_dict.items():
            cumulative += weight
            if rand_val <= cumulative:
                return key
        return list(pro_dict.keys())[-1]  # 处理浮点进度问题
    elif isinstance(pro_dict, list):
        keys = list(setting.choice_pro.keys())
        for key, weight in zip(keys, pro_dict[0]):
            cumulative += weight
            if rand_val <= cumulative:
                return key
        return keys[-1]
    else:
        print(f"[action]出现了不支持的数据类型{pro_dict}")
        return setting.choice_pro.keys()[-1]


def _action_file_signature(file_path):
    try:
        stat = os.stat(file_path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size, getattr(stat, 'st_ino', None)


_initial_action_file_signature = _action_file_signature(setting.action_file_path)


def server_choice(servers, is_interaction=True):
    """
    从数据服务器获取动作进行决策
    :param is_interaction 是否与数据服务器进行交互
    :return:
    """
    if is_interaction:  # 若与数据服务器进行交互，需要读取action文件中的数据,为了和下层动作一致，需要通过文件记录
        action_file = setting.action_file_path
        high_action_pro_list = setting.choice_pro
        current_signature = _action_file_signature(action_file)
        is_new_control = (current_signature is not None and
                          current_signature != _initial_action_file_signature)
        if is_new_control:
            try:
                with open(action_file, 'r', encoding='utf-8') as f:
                    action_dict = json.load(f)
                high_action_pro_list = action_dict['high_act_list']
                control_tag = action_dict['control_tag']
                setting.last_choice_pro = high_action_pro_list
                setting.last_control_tag = control_tag
            except Exception as e:
                print('读取新控制文件失败：', e)
                is_new_control = False

        if not is_new_control:
            if setting.last_choice_pro:
                high_action_pro_list = setting.last_choice_pro
                control_tag = setting.last_control_tag
                print(f'本轮控制文件不可用，沿用最近一次有效策略{high_action_pro_list}')
            else:
                print(f'本次运行尚无新控制下发，使用均匀策略{high_action_pro_list}')
                control_tag = 'S'

        # 保存目的概率字典，方便记录发送
        dst_pro_file_name = setting.dst_node_pro_file_path + setting.dst_node_pro_file_name
        os.makedirs(setting.dst_node_pro_file_path, exist_ok=True)
        pro_dict = list_to_dict(setting.choice_pro.keys(), high_action_pro_list)
        # 按概率选择服务器
        choice_key = weighted_random(high_action_pro_list)
        server = servers[choice_key]
        print(f'决策概率{high_action_pro_list},选择目标为{server}')
        up_action_dict = {'pro_dict': pro_dict, 'choice_key': choice_key}
        with open(dst_pro_file_name, 'w', encoding='utf-8') as f:
            json.dump(up_action_dict, f, ensure_ascii=False, indent=4)
        return server, control_tag
    else:
        pro_dict = setting.choice_pro  # 改成动作
        # 保存概率-方便记录
        # dst_pro_file_name = setting.dst_node_pro_file_path + setting.dst_node_pro_file_name
        # with open(dst_pro_file_name, 'w', encoding='utf-8') as f:
        #     json.dump(pro_dict, f, ensure_ascii=False, indent=4)
        choice_key = weighted_random(pro_dict)
        server = servers[choice_key]
        control_tag = 'S'
        return server, control_tag


def list_to_dict(keys_list, value_list):
    """
    列表转字典
    :param keys_list:
    :param value_list:
    :return:
    """
    new_dict = {}
    if isinstance(value_list, list):
        new_value_list = np.array(value_list).flatten().tolist()
        for key, value in zip(keys_list, new_value_list):
            new_dict[key] = value
        return new_dict
    elif isinstance(value_list, dict):
        return value_list
    else:
        print(f'不支持的数据类型{value_list}，请检查')
        raise TypeError(f'不支持的数据类型{value_list}，请检查')


if __name__ == '__main__':
    """
    测试ip获取功能
    """
    ip = get_ip_addr('ens32')
    print(ip)
    ma = IncrementalMovingAverage(100)

    data_stream = range(1000)  # 模拟数据流

    for val in data_stream:
        avg = ma.add(val)
        if avg is not None:
            print(f"Moving average: {avg}")






# if __name__ == '__main__':
#     """
#     测试获取路径特征
#
#     """
#     weight_file_path_prefix = setting.weight_file_path
#     weight_file_path = read_last_weight_file(weight_file_path_prefix)
#     path_file_path = setting.path_file_path
#     src = 1
#     dst = 15
#     path, weight_graph = get_path_and_weight_graph(src=src,dst=dst,path_file_path=path_file_path,weight_file_path=weight_file_path)
#     print(path)
#     print(path[0:-1], path[1:])
#     # print(weight_graph.edges(data=True))
#     path_feature_dataframe = compute_path_features(path,weight_graph)
#     print(path_feature_dataframe)
