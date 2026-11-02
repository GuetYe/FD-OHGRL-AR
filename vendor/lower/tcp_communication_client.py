#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/2/26 9:53
@File:tcp_communication_client.py
@Desc: 控制器与服务器，实现数据传输
"""
import socket
import struct
MAGIC_NUMBER = 0xDEADBEEF
from crc32c import crc32c
import msgpack
import msgpack_numpy
import networkx as nx


# # 将图数据转化为二进制数据，序列化和反序列化
# def graph_serialize(graph_data):
#     """
#     将图数据进行序列化：图数据--二进制
#     :param graph_data:
#     :return:
#     """
#     pass
#
# def send_graph_data(ip,port,graph_data):
#     """
#     传输图数据给控制器
#     :param ip: 控制器ip
#     :param port: 控制器监听端口
#     :param graph_data: 图数据
#     :return:
#     """
#     pass
#
# def call_back(res):
#     """
#     线程池回调函数
#     :param res:
#     :return:
#     """
#     pass
#
# def send_request(requst):
#     """
#     向服务器发送请求
#     :return:
#     """
#     pass


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



if __name__ == '__main__':
    server_ip = '172.25.6.239'
    port = 5002
    client = SocketClient(server_ip=server_ip,port=port)
    client.performance_test()





