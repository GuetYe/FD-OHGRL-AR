#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/16 15:47
@File:path_feature_server.py
@Desc:整理并传送路径性能
注意：运行之前必须要开启本地物理机上的服务端
    该程序应该还能使用在服务器上，供物理机请求路径和目标地址，后面进行完善
"""
import json
import os
import struct
import threading
from crc32c import crc32c
import setting

from utils import get_ip_addr
import socket
import pandas as pd
import copy
import msgpack


MAGIC_NUMBER = 0xDEADBEEF  # 用于验证的密码

class PathFeatureServer:
    def __init__(self,server_ip,port):
        """
        本地物理机作为服务器
        :param server_ip: 本地物理机的ip
        :param port: 端口
        """
        # 启动监听
        self.seq_num = 0  # 用来记录发送的数据包数
        self.server = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.server.bind((server_ip,port))
        self.server.listen(5)  # 启动监听
        print(f'数据服务器启动：{server_ip}:{port}')
        self.lock = threading.Lock()   # 添加线程锁

    def start(self):
        """
        使用多线程进行服务
        :return:
        """
        while True:
            client,addr = self.server.accept()
            print(f'新客户端链接：{addr}')
            threading.Thread(target=self.handle_client,args=(client,)).start()

    def handle_client(self,client):
        """
        对用户端的处理
        :param client:
        :return:
        """
        # 1.接受用户端的请求信息
        try:
            while True:
                # 1. 接收包头
                header = client.recv(16)
                if len(header) < 16:
                    break
                magic, recv_seq, data_len, crc = struct.unpack('>IIII',header)
                # 2. 数据包校验
                if magic != MAGIC_NUMBER:
                    print('非法请求')
                    break
                # 3. 检验通过后，接收数据体
                data = b''
                while len(data) < data_len:
                    packet = client.recv(data_len-len(data))
                    if not packet:
                        break
                    data += packet   # 接收剩余数据

                # 4. 校验数据
                if crc32c(data) != crc:
                    print('数据校验失败')
                    continue

                if data == setting.request_path_feature_ins:
                    # print('数据传输开始')
                    with self.lock:  # 确保线程安全
                        data_features_dict = self.get_path_feature()
                        # 分层发送数据
                        # print('需要发送的数据：', data_features_dict)
                        self.safe_sendall(data_features_dict)

        except (ConnectionResetError,BrokenPipeError) as e:
            print(f'客户端非正常断开:{str(e)}')
        except Exception as e:
            print(f'未知错误：{str(e)}')

        finally:
            client.close()

    def sent_data_feature(self,dict_data):
        """
        发送数据
        :param dict_data: 字典类型的数据
        :return:
        """
        serialize_data = self.serialize_dict_data(dict_data)
        header = struct.pack('>IIII',MAGIC_NUMBER,self.seq_num,len(serialize_data),crc32c(serialize_data))
        self.server.sendall(header+serialize_data)
        print('数据传输结束')
        self.seq_num += 1

    def is_connection_alive(self):
        """
        连接状态预判
        :return:
        """
        try:
            # 发送0字节探测包(Linux)
            self.server.send(b'',socket.MSG_DONTWAIT|socket.MSG_NOSIGNAL)
            return True
        except (ConnectionResetError,BrokenPipeError,OSError):
            return False


    def safe_sendall(self,dict_data,chunk_size=1024):
        """
        使用分层发送：数据分块传输
        :param dict_data:
        :return:
        """
        try:
            serialize_data = self.serialize_dict_data(dict_data)
            header = struct.pack('>IIII', MAGIC_NUMBER, self.seq_num, len(serialize_data), crc32c(serialize_data))
            print('发送包头',header)
            self.server.sendall(header)
            print('待发送的数据',serialize_data)
            # 分块发送主体数据
            for offset in range(0,len(serialize_data),chunk_size):
                chunk = serialize_data[offset:offset+chunk_size]
                # if not self.is_connection_alive():
                #     raise BrokenPipeError('连接已中断')
                self.server.sendall(chunk)
        except (BrokenPipeError,ConnectionResetError) as e:
            print(f'发送中断:{e}')
            raise



    def serialize_dict_data(self, dict_data):
        """
        图数据序列化：原图数据--->二进制
        :param dict_data:
        :return:
        """
        dict_data_packed = msgpack.packb(dict_data, use_bin_type=True)
        return dict_data_packed



    def get_path_feature(self):
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
                with open(file_path, 'r', encoding='utf-8') as f:
                    path_feature_dict = json.load(f)
                    # print(type(path_feature_dict))
                    # print(path_feature_dict.keys())
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
        return path_features_dict



if __name__ == '__main__':
    # ip = get_ip_addr('ens32')
    ip = '0.0.0.0'
    port = 5003
    print(f'ip: {ip}, port: {port}')
    server = PathFeatureServer(server_ip=ip,port=port)
    server.start()