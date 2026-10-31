#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/2/19 16:39
@File:anycast_tcp_client.py
@Desc:****************
"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor

# 生成相应大小的数据包
def generate_large_packet(size_kb):
    """
    模拟数据包的生成
    :param size_kb: 模拟数据包的大小,单位为kb
    :return:
    """
    return b'x'*(size_kb*1024)+b'\n'

# 向服务器端发送数据包
def send_large_packet(ip,port,packet):
    """
    模拟数据包的发送
    :param ip: 服务端的ip
    :param port: 服务端的端口
    :param packet: 需要发送的数据包
    :return: 返回数据包端到端时延
    """
    d2d_time = None  # 默认是None
    try:
        # 创建socket
        client_socket = socket.socket()
        client_socket.connect((ip,port))
        print(f'已连接到服务端{ip}：{port}')
        # 发送数据包
        send_time = time.time()
        client_socket.send(packet)
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
    except Exception as e:
        print(f'与服务端{ip}：{port}通信时发生错误：{e}')
    return d2d_time

def call_back(res):
    print('本次端到端时延是:',res.result())  # 回调函数


if __name__ == '__main__':
    # 定义服务端地址和端口
    servers = [
        ('192.168.0.15', 5001),
    ]
    # 生成1000 kB的数据包
    # 模拟发送50个数据包的情况
    packet_nums = 50
    pool = ThreadPoolExecutor(10)  # 不传参的话，默认开设的线程数量，是当前cpu的个数乘以5
    for _ in range(packet_nums):
        large_packet = generate_large_packet(20)
        # 向多个服务器发送数据包
        server = servers[0]
        ip, port = server
        pool.submit(send_large_packet,ip,port,large_packet).add_done_callback(call_back)  # 通过回调机制获取结果
        # send_large_packet(ip,port,large_packet)  # 通过线程池的形式实现多客户端数据的并发