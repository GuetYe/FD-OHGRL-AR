#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/20 22:37
@File:d2d_delay_fit_train.py
@Desc:端到端时延拟合函数
"""
from model_utils import PostDataset,DataPreprocessing
import setting
from algorithm_models import E2EDelayModel
from model_utils import E2ENetworkTrainer
import random
import torch
import numpy as np
random.seed(1234)
torch.manual_seed(1234)
np.random.seed(1234)


if __name__ == '__main__':
    # 数据准备
    data_path = setting.total_path_features_file_server_path + setting.total_path_features_file_name
    data_preprocess = DataPreprocessing(data_path)
    X_norm_train, y_norm_train = data_preprocess.get_data('train')
    dataset = PostDataset(X_norm_train, y_norm_train)

    # 模型初始化
    input_size = len(setting.feature_addr)
    model = E2EDelayModel(input_size=input_size, hidden_dims=[64, 32])


    # 训练模型
    trainer = E2ENetworkTrainer(model=model, dataset=dataset, batch_size=32)
    trainer.train(epochs=300, to_plot=True)

    # 模型应用
    X_norm_eval, y_norm_eval = data_preprocess.get_data('eval')
    eval_dataset = PostDataset(X_norm_eval, y_norm_eval)
    trainer.predict_app(eval_dataset, to_plot=True)

    # 模型应用
    X_norm_eval, y_norm_eval = data_preprocess.get_data('train')
    eval_dataset = PostDataset(X_norm_eval, y_norm_eval)
    trainer.predict_app(eval_dataset, to_plot=True)

