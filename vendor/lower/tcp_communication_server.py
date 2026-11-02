#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/2/26 15:52
@File:tcp_communication_server.py
@Desc:本地物理机和服务器通信，服务器端
           +----------------+          +----------------+
           |  本地客户端     |  <-----> |  服务端         |
           | - 上传图数据    |  双工通信 | - 实时路径计算   |
           | - 接收路径结果  |          | - 状态监控      |
           +----------------+          +----------------+
"""
import socket
import struct
import threading
from crc32c import crc32c
import networkx as nx
import msgpack

MAGIC_NUMBER = 0xDEADBEEF
class SocketServer:
    def __init__(self,server_ip,port):
        """
        服务器与本机通信，服务器端
        :param server_ip: 服务器ip
        :param port: 端口
        """
        # 1 启动监听
        self.server = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.server.bind((server_ip,port))
        self.server.listen(5)  # 启动监听
        print(f'服务器端链接：{server_ip}:{port}')

    def start(self):
        """
        处理接收到的数据
        :return:
        """
        while True:
            client, addr = self.server.accept()
            print(f'新客户端链接:{addr}')
            threading.Thread(target=self.handle_client,args=(client,)).start()

    def handle_client(self,client):
        seq_sum = 0
        try:
            while True:
                # 接收包头
                header = client.recv(16)
                if len(header)<16:
                    break

                magic, recv_seq, data_len, crc = struct.unpack('>IIII', header)

                if magic != MAGIC_NUMBER:
                    print("非法数据包")
                    break

                # 接收数据体
                data = b''
                while len(data)<data_len:
                    packet = client.recv(data_len-len(data))
                    if not packet:
                        break
                    data += packet    # 接收剩余数据

                # 校验数据
                if crc32c(data) != crc:
                    print('数据校验失败')
                    continue

                # 反序列化数据图数据
                graph = self.deserialize_graph(data)

                # 保存图数据


                # 数据的使用，后面写
                print(graph.nodes())
                print(graph.edges())

        except ConnectionResetError:
            print("客户端断开连接")
        finally:
            client.close()


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
            graph.add_node(n['id'],**{k:v for k,v in n.items() if k != 'id'})  # 反序列节点属性
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

if __name__ == '__main__':
    ip = '172.25.6.239'
    port = 5002
    server = SocketServer(server_ip=ip,port=port)
    server.start()




