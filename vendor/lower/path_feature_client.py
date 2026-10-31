#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/16 22:48
@File:path_feature_client.py
@Desc:发出数据请求，将获得的数据转化成pd.DataFrame类型
"""
import socket
import setting
import struct
from crc32c import crc32c
import msgpack
import pandas as pd

MAGIC_NUMBER = 0xDEADBEEF
class PathFeatureClient:
    def __init__(self,server_ip='192.168.85.132', port=5003):
        """
        连接数据服务器，下载数据
        ip: 192.168.85.132, port: 5003
        连接服务器，需要ip和端口
        :param server_ip:
        :param port:
        """
        # 通过socket建立连接
        self.client = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.client.connect((server_ip, port))
        print(f'与服务器{server_ip}:{port}建立连接')
        self.seq_num = 0

    def send_path_features_request(self):
        """
        发送数据请求
        :return:
        """
        serialize_data = setting.request_path_feature_ins
        header = struct.pack('>IIII',MAGIC_NUMBER, self.seq_num, len(serialize_data), crc32c(serialize_data))
        self.client.sendall(header+serialize_data)
        print('发送数据请求，请稍后。。。')
        self.seq_num += 1


    def serialize_dict_data(self, data):
        """
        数据--->二进制
        :param data:
        :return:
        """
        dict_data_packed = msgpack.packb(data, use_bin_type=True)
        return dict_data_packed

    def recv_loop(self):
        """
        接收数据
        :return:
        """
        while True:
            try:
                header = self.client.recv(16)
                print(header)
                assert len(header) >= 16, f'数据{header}长度异常！'
                magic, seq, data_len, crc = struct.unpack('>IIII',header)

                data = b''

                while len(data) < data_len:
                    chunk = self.client.recv(data_len-len(data))
                    if not chunk:
                        break
                    data += chunk

                if crc32c(data) == crc:
                    path_features_dict = msgpack.unpackb(data,raw=False)  # 解析数据--字典
                    # 将字典转化为DataFrame
                    path_features_dataframe = pd.DataFrame(path_features_dict)  # 字典转化为 -- dataframe
                    return path_features_dataframe
                else:
                    print('非法数据！')
                    return None

            except ConnectionAbortedError:
                print('连接断开！')
                return None

    def preformance_test(self):
        self.send_path_features_request()
        path_features_dataframe = self.recv_loop()
        print('获得数据前10行', path_features_dataframe.head(10))
        return path_features_dataframe

if __name__ == '__main__':
    # server_ip = '192.168.85.132'
    # port = 5003

    server_ip = '10.33.96.125'
    port = 8900
    client = PathFeatureClient(server_ip=server_ip,port=port)
    client.preformance_test()


