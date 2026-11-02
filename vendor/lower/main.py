#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time: 2024/10/16 15:47
@File: main.py
@Decs:*********************
"""
import os
import sys

from mn_wifi.sumo.sumolib.geomhelper import distance

curr_path = os.path.dirname(os.path.abspath(__file__))  # =sys.path[0]也可以
parent_path = os.path.dirname(curr_path)
sys.path.append(parent_path)

import time
import xml.etree.ElementTree as ET
import networkx as nx

from mn_wifi.topo import Topo
from mn_wifi.net import Mininet_wifi
from mininet.node import RemoteController
from mn_wifi.link import wmediumd
from mininet.node import UserSwitch
from mininet.link import TCLink
from mn_wifi.cli import CLI
from mininet.log import setLogLevel, info
from mininet.util import dumpNodeConnections


def generate_ap_port(graph):
    """ 根据图中的节点和连接，生成每个p（节点）的端口编号 """
    ap_port_dict = {}
    for node in graph.nodes:
        # graph.degree[node]获取图中节点的度（degree）,与节点相连的边的数目
        ap_port_dict.setdefault(node, list(range(graph.degree[node])))
        # print(node, list(range(graph.degree[node])))
    return ap_port_dict


def parse_wifi_xml_topology(topology_path):
    """
    解析存储在 XML 文件中的 WiFi 网络拓扑，生成一个 networkx.Graph() 对象，并提取节点、边的信息以及每个 AP 和 STA 的位置坐标
    :param topology_path: 拓扑图 graph，网络节点数量 nodes_num，网络链路数量 edges_num
    :return: 拓扑图 graph，网络节点数量 nodes_num，网络链路数量 edges_num、 AP 和 STA 的位置
    """
    tree = ET.parse(topology_path)  # 使用 ElementTree 解析 XML 文件
    root = tree.getroot()  # 获取 XML 文件的根节点对象
    topo_elemenr = root.find('topology')
    graph = nx.Graph()  # 创建一个空的无向图
    ap_location_lst = []  # 用于存储 AP(无线接入点) 的位置
    sta_location_lst = []  # 用于存储 STA(通过无线连接到 AP 的终端设备) 的位置
    for child in topo_elemenr.iter():
        # 解析节点
        if child.tag == 'node':  # 1-5 为ap
            node_id = int(child.get('id'))  # 从 'node' 标签中提取 id 属性
            graph.add_node(node_id)  # 将节点添加到图中
            # 解析AP和STA的坐标
            ap_location_lst.append(
                str(child.find('locationap').get('coordinate')))  # 从当前节点中查找 locationap 子元素并获取其 coordinate 属性，表示 AP 的坐标
            if len(list(child)) > 1:
                # 添加进列表
                for i in range(len(list(child)) - 1):
                    sta_location_lst.append(str(child.find(f'locationsta{i}').get('coordinate')))
            else:
                pass
        elif child.tag == 'link':
            from_node = int(child.find('from').get('node'))
            to_node = int(child.find('to').get('node'))
            graph.add_edge(from_node, to_node)
    # 计算节点和边的数量
    nodes_num = len(graph.nodes)
    edges_num = len(graph.edges)
    print('节点数量nodes_num: ', nodes_num, ',  节点信息graph.nodes: ', graph.nodes, '\n', '边数量edges_num: ',
          edges_num, ',  边信息graph.edges: ', graph.edges)
    print('AP(无线接入点)的位置列表:', ap_location_lst, '\n存储STA(通过无线连接到 AP 的终端设备)的列表:',
          sta_location_lst)
    # 返回图对象、节点数、边数以及 AP 和 STA 的位置
    return graph, nodes_num, edges_num, ap_location_lst, sta_location_lst


def cal_ap_distance(ap1_location, ap2_location):
    # 求解两点间距
    # ['30,50,0', '80,110,0', '90,50,0', '120,50,0', '150,50,0']
    ap1_x, ap1_y, ap1_z = ap1_location.split(',')
    ap2_x, ap2_y, ap2_z = ap2_location.split(',')

    x = abs(int(ap1_x) - int(ap2_x))
    y = abs(int(ap1_y) - int(ap2_y))

    distance = pow(x ** 2 + y ** 2, 0.5)
    return distance


class Nodes5Topo():
    def __init__(self, graph, ap_location, sta_location):
        "Create a network."

        self.net = Mininet_wifi(controller=RemoteController, link=TCLink, waitConnected=True, build=False)

        self.graph = graph  # 图对象，表示整个网络拓扑结构，包括节点（如交换机、接入点、主机等）和边（连接这些节点的链路）
        self.node_idx = graph.nodes  # 返回网络图中所有节点的集合,保存了这些节点的索引（节点的唯一标识符），用于后续的网络操作或路径计算。[1, 2, 3, 4, 5]
        self.edges_pairs = graph.edges  # 保存这些连接的配对关系，代表了网络中的链路信息，例如两个AP之间的链路或AP与STA之间的连接。[(1, 2), (1, 3), (1, 4), (2, 5), (3, 5), (4, 5)]

        self.bw = 10
        self.random_bw = 40  # Gbps -> M * 10  代表网络中链路的随机带宽，单位为Gbps
        self.bw4 = 50  # host -- switch  主机与交换机之间的固定带宽

        self.delay = 1  # 时延： 1ms
        self.loss = 1  # 丢包率：1%

        self.host_port = 9  # 是与主机连接的端口号，表示某个网络设备（如交换机或接入点）用于连接主机的接口编号。
        self.snooper_port = 10  # 嗅探器端口号，代表网络中用于监听或捕获数据包的特定端口
        self.ap_position = ap_location  # ['30,50,0', '80,110,0', '90,50,0', '120,50,0', '150,50,0']
        self.sta_position = sta_location  # ['10,30,0', '15,35,0', '20,40,0', '50,60,0']

    def topology(self):
        info("*** Creating Nodes ***\n")

        aps = {}
        stas = {}
        for index in self.node_idx:  # node_idx=[1, 2, 3, 4, 5]
            if index <= len(self.sta_position):
                # 添加AP
                ap = self.net.addAccessPoint(f'ap{index}', ssid=f'ssid_ap{index}', mode='g', channel='5',
                                             position=str(self.ap_position[index - 1]), mac=f'{index}0:00:00:00:00:00')
                aps.setdefault(index, ap)
                print('添加AP:', index)

                # 添加sta
                sta = self.net.addStation(f'sta{index}', mac=f'00:00:00:00:00:0{index}', ip=f'192.169.0.{index + 1}/24',
                                          position=self.sta_position[index - 1])
                stas.setdefault(index, sta)
                print('添加sta:', index)
            else:
                # 添加AP
                ap = self.net.addAccessPoint(f'ap{index}', ssid=f'ssid_ap{index}', mode='g', channel='5',
                                             position=str(self.ap_position[index - 1]), mac=f'{index}0:00:00:00:00:00')
                aps.setdefault(index, ap)
                print('添加AP:', index)

        print(aps, '\n', stas)
        print('-----------------------------------------------')

        print(" ------------------ Configuring Propagation Model ------------------ ")
        # 网络的传播模型设置为对数距离模型（logDistance），并指定衰减指数（exp=4.5）
        self.net.setPropagationModel(model="logDistance", exp=4.5)
        # 表明即将配置网络中的节点。然后调用 configureNodes 方法，通常用于为网络中的各个节点（例如接入点和站点）分配 IP 地址、无线接口或其他网络属性
        print(" ------------------ Configuring nodes ------------------ ")
        self.net.configureNodes()

        # ap 之间添加 link
        print(' ------------------ Adding Ap Links ------------------')
        # 创建端口用于AP间连接
        ap_port_dict = generate_ap_port(
            self.graph)  # ap_port_dict={1: [0, 1, 2], 2: [0, 1], 3: [0, 1], 4: [0, 1], 5: [0, 1, 2]}
        links_info = {}  # 存储链路信息
        for l in self.edges_pairs:  # [(1, 2), (1, 3), (1, 4), (2, 5), (3, 5), (4, 5)]
            _distance = round(cal_ap_distance(self.ap_position[l[0] - 1], self.ap_position[
                l[0] - 1]))  # ['30,50,0', '80,110,0', '90,50,0', '120,50,0', '150,50,0']
            port1 = ap_port_dict[l[0]].pop(0) + 1
            port2 = ap_port_dict[l[1]].pop(0) + 1
            # 指定链路类为 wmediumd，这是一个模拟无线链路质量的模块，用于为链路设置无线特性。
            self.net.addLink(aps[l[0]], aps[l[1]], cls=wmediumd, port1=port1, port2=port2, bw=self.bw, delay=self.delay,
                             loss=self.loss)
            links_info.setdefault(l, {"port1": port1, "port2": port2, "free_bw": self.bw, "delay": self.delay,
                                      "loss": self.loss, "used_bw": 0, "pkt_err": 0, "pkt_drop": 0,
                                      "distance": _distance})

        # 创建端口用于 ap连接sta
        ap_port_dict = generate_ap_port(
            self.graph)  # ap_port_dict={1: [0, 1, 2], 2: [0, 1], 3: [0, 1], 4: [0, 1], 5: [0, 1, 2]}

        # AP<--->STA
        for sta in stas.keys():
            print(sta)
            if int(sta) <= 3:
                self.net.addLink(stas[sta], aps[1], port1=0, port2=ap_port_dict[1][-1] + 2 + sta, cls=TCLink,
                                 bw=self.bw, delay=self.delay, loss=self.loss)
            else:
                self.net.addLink(stas[sta], aps[5], port1=0, port2=ap_port_dict[1][-1] + 2 + sta, cls=TCLink,
                                 bw=self.bw, delay=self.delay, loss=self.loss)

        info("*** Starting network\n")
        c0 = self.net.addController('c0', ip='127.0.0.1', port=6653)  # 添加远程控制器
        self.net.build()  # 建立网络

        self.net.start()  # 启动网络

        print("获取stas设备列表")
        print("主机信息：", stas)
        print("===显示主机连接信息")
        dumpNodeConnections(self.net.stations)  # 显示主机连接信息

        CLI(self.net)
        self.net.stop()


if __name__ == '__main__':
    topology_path = './topology.xml'
    graph, nodes_num, edges_num, ap_location_lst, sta_location_lst = parse_wifi_xml_topology(topology_path)
    print('======================================')
    mytopo = Nodes5Topo(graph, ap_location_lst, sta_location_lst)
    mytopo.topology()
