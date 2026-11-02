#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/3/19 11:31
@File:algorithm_models.py
@Desc:算法模型模块
"""
# 端到端时延神经网络
"""
输入：带宽，时延，丢包率, 队列信息
"""
import torch.nn as nn

class E2EDelayModel(nn.Module):
    def __init__(self, input_size=4, hidden_dims=[64,32]):
        super(E2EDelayModel, self).__init__()
        layers = []   # 初始化层信息
        prev_dim = input_size  # 输入大小

        # 动态构建隐藏层
        for dim in hidden_dims:
            layers.extend(
                [
                    nn.Linear(prev_dim,dim),
                    nn.BatchNorm1d(dim),
                    nn.Tanh(),
                    nn.Dropout(0.5)
                 ]
            )
            prev_dim = dim

        self.feature_extractor = nn.Sequential(*layers)  # 将列表变为模型块
        self.regressor = nn.Linear(prev_dim, 1)  # 最后的输出层

    def forward(self, x):
        features = self.feature_extractor(x)  # 整个隐藏层
        return self.regressor(features)  # 输出层







