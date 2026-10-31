#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
@Auther:Evans_WP
@Contact:WenPeng19943251019@163.com
@Time:2025/3/19 11:29
@File:model_utils.py
@Desc:包含了数据处理模块，训练方式
"""
import setting
from torch.utils.data import Dataset,DataLoader
import json
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
import time
import math

# 读取数据端到端数据集
class DataPreprocessing:
    def __init__(self,data_path,train_rate=0.8):
        """
        数据预处理，包括数据划分，归一化和反归一化
        :param data_path: 数据路径
        :param train_rate: 训练率
        """
        self.data_path = data_path  # 数据路径
        self.train_rate = train_rate  # 用于训练的比例
        self.path_features_dataframe = self.dict_to_dataframe()  # 获取dataframe类型的数据
        # self.X, self.y, self.X_mean, self.X_std, self.y_mean, self.y_std = self.basic_feature_extraction()  # 数据基本特征的提取
        self.X, self.y, self.X_max, self.X_min, self.y_max, self.y_min = self.basic_feature_extraction()  # 数据基本特征的提取
        self.X_norm, self.y_norm = self.normalization()  # 数据归一化



    def get_data(self,app_type='train'):
        """
        实现数据分割，获取相应的数据
        :param app_type: 需要的数据类型
        :return:
        """
        data_num = len(self.path_features_dataframe.index)
        train_data_num = math.floor(data_num * self.train_rate)
        if app_type == 'train':  # 如果用于训练
            X_norm_train = self.X_norm.iloc[0:train_data_num]
            y_norm_train = self.y_norm.iloc[0:train_data_num]
            return X_norm_train, y_norm_train
        else:
            X_norm_eval = self.X_norm.iloc[train_data_num:]
            y_norm_eval = self.y_norm.iloc[train_data_num:]
            return X_norm_eval, y_norm_eval


    def dict_to_dataframe(self):
        """
        字典数据转化为dataframe
        :return:
        """
        with open(self.data_path, 'r', encoding='utf-8') as f:
            path_features_dict = json.load(f)

        path_features_dataframe = pd.DataFrame(path_features_dict)
        return path_features_dataframe

    def basic_feature_extraction(self):
        """
        获取数据的基本特征，包括特征数据和标签数据的平均值和标准差
        :return:
        """
        y = self.path_features_dataframe[setting.label_addr]
        X = self.path_features_dataframe[setting.feature_addr]

        # 正态归一化，会导致数据不在0-1范围，不方便拟合
        # y_mean, y_std = y.mean(), y.std()
        # X_mean, X_std = X.mean(axis=0), X.std(axis=0)
        # 最大最小归一化，可以实现在0-1范围
        y_max, y_min = y.max(), y.min()
        X_max, X_min = X.max(axis=0), X.min(axis=0)

        return X, y, X_max, X_min, y_max, y_min

    def normalization(self):
        """
        数据归一化操作
        :return:
        """
        # y_norm = (self.y - self.y_mean) / self.y_std
        # X_norm = (self.X - self.X_mean) / self.X_std
        y_norm = (self.y - self.y_min) / (self.y_max-self.y_min)
        X_norm = (self.X - self.X_min) / (self.X_max-self.X_min)
        return X_norm, y_norm

# 对数据集进行Dataset封装
class PostDataset(Dataset):
    def __init__(self,features,index):
        """
        将数据封装成Dataset的形式
        :param features:
        :param index:
        """
        super(PostDataset, self).__init__()
        self.features = features
        self.index = index

    def __len__(self):
        assert len(self.index.index) == len(self.features.index), f'数据长度不一，请检查。。。'
        return len(self.features.index)


    def __getitem__(self,idx):
        return (
            torch.FloatTensor(self.features.iloc[idx].values),
            torch.FloatTensor([self.index.iloc[idx]])
        )






# # 自定义数据集生成类
# class PathFeatureDataset(Dataset):
#     def __init__(self, data_path, train_rate=0.8):
#         """
#         生成数据集
#         :param data_path: 数据路径
#         :param app_type: 应用类型{'train','eval'},训练或测试
#         :param train_rate: 用于训练的数据比例，默认为80%
#         """
#         self.train_rate = train_rate
#         super(PathFeatureDataset, self).__init__()
#         self.data_path = data_path  # 数据路径
#         self.path_features_dataframe = self.dict_to_dataframe()
#         print(self.path_features_dataframe.head(10))
#         self.y, self.X = self.normalization()
#         # 记录数据的均值和标准差，用于数据的反归一化
#         self.X_mean = None
#         self.X_std = None
#         self.y_mean = None
#         self.y_std = None
#
#
#     def dict_to_dataframe(self):
#         """
#         字典数据转化为dataframe
#         :return:
#         """
#         with open(self.data_path, 'r', encoding='utf-8') as f:
#             path_features_dict = json.load(f)
#
#         path_features_dataframe = pd.DataFrame(path_features_dict)
#         return path_features_dataframe
#
#
#     def normalization(self, app_type='train'):
#         y = self.path_features_dataframe[setting.label_addr]
#         X = self.path_features_dataframe[setting.feature_addr]
#
#         self.y_mean, self.y_std = y.mean(), y.std()
#         self.X_mean, self.X_std = X.mean(axis=0), X.std(axis=0)
#
#         y_norm = (y - self.y_mean)/self.y_std
#         X_norm = (X - self.X_mean)/self.X_std
#
#         data_num = len(self.path_features_dataframe.index)
#         train_data_num = math.floor(data_num * self.train_rate)
#         eval_data_num = data_num - train_data_num
#         # if app_type == 'train':  # 如果用于训练
#         #     X_norm = X_norm.iloc[0:train_data_num]
#         #     y_norm = y_norm.iloc[0:train_data_num]
#         # else:
#         #     X_norm = X_norm.iloc[train_data_num:]
#         #     y_norm = y_norm.iloc[train_data_num:]
#         return y_norm, X_norm
#
#     def in_normalization(self, y_norm, X_norm=None):
#         """
#         反归一化操作
#         :param y_norm: 归一化后的值
#         :return: 反归一化的值
#         """
#         y = y_norm * self.y_std + self.y_mean
#
#         if X_norm:
#             X = X_norm * self.X_std + self.y_mean
#             return y,X
#
#         return y
#
#     def __len__(self):
#         return len(self.path_features_dataframe.index)
#
#
#     def __getitem__(self,idx):
#         return (
#             torch.FloatTensor(self.X.iloc[idx].values),
#             torch.FloatTensor([self.y.iloc[idx]])
#         )


# 训练网络模型类
class E2ENetworkTrainer:
    def __init__(self, model, dataset, batch_size):
        self.model = model
        self.dataloader = DataLoader(dataset, batch_size, shuffle=True)
        self.criterion = nn.MSELoss()
        self.optimizer = optim.Adam(model.parameters(), lr=0.0001, weight_decay=1e-4)
        self.scheduler = optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, 'min')  # 梯度下降法，结果越小越好

    def train(self, epochs, to_plot = False):
        """
        训练过程
        :param epochs: 训练代数
        :return:
        """
        self.model.train()  # 模型训练阶段
        epochs_list = []  # epoch列表
        avg_loss_list = []  # 平均loss列表
        for epoch in range(epochs):
            total_loss = 0.0  # 初始化loss
            for X_batch, y_batch in self.dataloader:
                self.optimizer.zero_grad()   # 梯度清零
                outputs = self.model(X_batch)
                loss = self.criterion(outputs, y_batch)
                loss.backward()
                # nn.utils.clip_grad_norm(self.model.parameters(), 1.0)
                self.optimizer.step()
                total_loss += loss.item()  # 求累加loss

            avg_loss = total_loss/len(self.dataloader)  # 求平均loss
            self.scheduler.step(avg_loss)
            avg_loss_list.append(avg_loss)
            epochs_list.append(epoch+1)
            print(f'Epoch {epoch+1}/{epochs} | Loss: {avg_loss:.4f} ')

        model_save_path = setting.model_save_path
        model_save_path = model_save_path+'model'+\
                time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())+'.pth'

        torch.save(self.model, model_save_path)

        if to_plot:
            plt.plot(epochs_list, avg_loss_list)
            plt.show()


    def predict_app(self, eval_dataset, to_plot=False):
        self.model.eval()  # 测试模块
        dataloader = DataLoader(eval_dataset, 1, shuffle=False)
        predict_value_list = []  # 预测值
        original_value_list = []  # 原始值
        count_list = []   # 数量序列
        count = 0  # 计数器
        for X_bach, y_batch in dataloader:
            predict_value = self.model(X_bach)
            predict_value_list.append(float(predict_value))
            original_value_list.append(float(y_batch))
            count_list.append(count)
            count += 1

        if to_plot:
            plt.plot(count_list, predict_value_list, 'r*--', label='predict_value')
            plt.plot(count_list, original_value_list, 'bo-.', label='original_value')
            print('预测值:', predict_value_list)
            print('原始值:',original_value_list)
            plt.show()


if __name__ == '__main__':
    # 数据路径
    data_path = setting.total_path_features_file_server_path + setting.total_path_features_file_name
    data_preprocess = DataPreprocessing(data_path)
    X_norm_train, y_norm_train = data_preprocess.get_data('train')
    print(X_norm_train)
    print(y_norm_train)
    dataset = PostDataset(X_norm_train, y_norm_train)






    # path_features = PathFeatureDataset(data_path)
    # path_features_dataloader = DataLoader(path_features,2,shuffle=True)
    # for X_batch,y_batch in path_features_dataloader:
    #     print(X_batch)
    #     print(y_batch)
