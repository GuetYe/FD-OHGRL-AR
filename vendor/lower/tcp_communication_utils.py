#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/3/31
@File:tcp_communication_utils.py
@Desc: ryu实时将状态数据发送给服务器工具
"""
import socket
import struct
MAGIC_NUMBER = 0xDEADBEEF
from crc32c import crc32c
import msgpack
import msgpack_numpy
import networkx as nx
import setting
import json
import copy

class SocketClient:
    def __init__(self,server_ip,port):
        """
        连接服务器，需要ip和端口
        :param server_ip:服务器ip
        :param port:端口
        """
        # 通过socket建立连接
        self.sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.sock.connect((server_ip,port))
        print(f'与服务器{server_ip}:{port}建立连接')
        self.seq_num = 0  # 包序号计数器
        self.src_node_list = setting.src_node_list  # 源节点列表[1]
        self.dst_node_list = setting.dst_node_list  # 目的节点列表[15,16,17,18]

    def sent_graph_path_state(self, graph_data, path_file):
        """
        在图属性的基础上添加路径的标识
        :param graph_data:
        :param path_file:
        :return:
        """
        with open(path_file, 'r', encoding='utf-8') as f:
            path_dict = json.load(f)

        new_graph_data = copy.deepcopy(graph_data)  # 对数据进行深拷贝，方便进行修改

        # 遍历边的信息
        for src in self.src_node_list:
            for dst in self.dst_node_list:
                key_name = f'({src},{dst})'  # 键名
                _path = path_dict[key_name]   # 获取路径
                _path_set = [(_src,_dst) for _src, _dst in zip(_path[:-1], _path[1:])]
                for u, v in graph_data.edges():
                    if (u,v) in _path_set:
                        new_graph_data[u,v][key_name] = 1
                    else:
                        new_graph_data[u,v][key_name] = 0


        self.sent_graph(new_graph_data)


    def sent_graph(self,graph_data):
        """
        发送图数据
        :param graph_data: 原图数据
        :return:
        0        4        8        12       16
        +--------+--------+--------+--------+
        |  MAGIC | SEQ_ID | LENGTH |  CRC32 |
        +--------+--------+--------+--------+
        """
        serialize_graph_data = self.serialize_graph(graph_data)
        header = struct.pack('>IIII',MAGIC_NUMBER,self.seq_num,len(serialize_graph_data),crc32c(serialize_graph_data))
        self.sock.sendall(header + serialize_graph_data)
        print('数据传输结束')
        self.seq_num += 1
        #  物理机                          服务器
        # 原图数据 ---> 序列化二进制 ---> 二进制图数据 ---> 反序列化

    def recv_loop(self):
        """
        接收结果
        :return:
        """
        while True:
            try:
                header = self.sock.recv(16)
                assert len(header) >= 16, '数据长度异常！'
                magic, seq, data_len, crc = struct.unpack('>IIII',header)

                data = b''

                while len(data) < data_len:
                    chunk = self.sock.recv(data_len-len(data))   # 解析剩余数据
                    if not chunk:
                        break
                    data += chunk

                if crc32c(data) == crc:
                    print(f"收到的数据结果为：{data}")
            except ConnectionAbortedError:
                break


    def serialize_graph(self,graph_data):
        """
        图数据序列化：原图数据--->二进制
        :param graph_data:
        :return:
        """
        graph_packed = msgpack.packb(self.networkx_to_dict(graph_data), use_bin_type=True)
        return graph_packed

    def deserialize_graph(self,serialize_graph_data):
        """
        图数据反序列化：二进制--->图数据
        :param serialize_graph_data:
        :return:
        """
        data = msgpack.unpackb(serialize_graph_data,raw=False)
        graph = nx.Graph()
        for n in data['nodes']:
            graph.add_node(n['id'],**{k:v for k,v in n.itrms() if k != 'id'})  # 反序列节点属性
        for e in data['edges']:
            graph.add_edge(e['source'],e['target'], **{k:v for k,v in e.items() if k not in ['source','target']})  # 反序列边属性
        return graph

    def networkx_to_dict(self,graph_data):
        """
        将图数据转化为字典
        :return:
        """
        return {
            "nodes": [{"id":n[0],**n[1]} for n in graph_data.nodes(data=True)],
            "edges": [{'source':u, "target":v, **d} for u,v,d in graph_data.edges(data=True)]
        }

    # 创建带权重的有向图
    def create_test_graph(self):
        # 初始化空图
        G = nx.DiGraph()

        # 添加带属性的节点
        nodes = [
            (1, {"color": "red", "size": 100}),
            (2, {"color": "blue", "size": 200}),
            (3, {"color": "green", "size": 150}),
            (4, {"color": "yellow", "size": 80}),
            (5, {"color": "purple", "size": 120}),
        ]
        G.add_nodes_from(nodes)

        # 添加带权重的边
        edges = [
            (1, 2, {"weight": 1.5}),
            (1, 3, {"weight": 2.0}),
            (2, 4, {"weight": 0.8}),
            (3, 4, {"weight": 1.2}),
            (4, 5, {"weight": 2.5}),
            (5, 1, {"weight": 0.5}),
        ]
        G.add_edges_from(edges)
        return G

    def performance_test(self):
        graph_data = self.create_test_graph()
        self.sent_graph(graph_data)

# Flask框架
from flask import Flask, jsonify
import threading
import time
app = Flask(__name__)










if __name__ == '__main__':
    server_ip = '172.25.6.242'
    port = 5002
    client = SocketClient(server_ip=server_ip,port=port)
    # client.performance_test()
    G = client.create_test_graph()






