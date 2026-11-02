#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/3/24 21:11
@File:d2d_delay_fit_tool.py
@Desc:****************
"""
import setting
from model_utils import DataPreprocessing
import numpy as np
import math
from sklearn import preprocessing
from sklearn.neural_network import MLPRegressor
from sklearn.model_selection import cross_val_score
import matplotlib.pyplot as plt
import pandas as pd
import pickle
import time
# np.random.seed(7)
import seaborn as sns

def compute_correlation(x, y):
    xbar = np.mean(x)
    ybar = np.mean(y)
    ssr = 0.0
    var_x = 0.0
    var_y = 0.0
    for i in range(0, len(x)):
        diff_xbar = x[i] - xbar
        dif_ybar = y[i] - ybar
        ssr += (diff_xbar * dif_ybar)
        var_x += diff_xbar ** 2
        var_y += dif_ybar ** 2
    sst = np.sqrt(var_x * var_y)
    return ssr / sst


if __name__ == '__main__':
    # 数据准备
    seed_result = []
    sample_num = 19000
    for seed in range(100):
        # if seed == 38: # d2d_time 预测最准确
        if seed == 7:
            np.random.seed(seed)
            data_path = setting.total_path_features_file_server_path + setting.total_path_features_file_name
            data_preprocess = DataPreprocessing(data_path)
            df = pd.concat([data_preprocess.X, data_preprocess.y], axis=1)
            # df = df.iloc[10000:sample_num]
            print(df.shape)
            # df = df.rename(columns={
            #     'forward_queue_pkts':'queue_pkts',
            #     'd2d_time':'E2E_delay'
            # })
            df = df.rename(columns={
                'free_bw': 'RBW',
                'delay': 'LAT',
                'loss':'PLR',
                'forward_queue_pkts':'PQL',
                'd2d_time':'FCT'
            })

            # 计算相关系数矩阵
            print("Pandas相关系数矩阵:")
            print(df.corr(method='pearson'))  # 可选method: pearson/spearman/kendall

            plt.figure(figsize=(10, 8))
            ax=sns.heatmap(df.corr(method='pearson'), annot=True, cmap='coolwarm', vmin=-1, vmax=1,
                        annot_kws={'size': 14},  # 标注数字的字体大小（默认约8，可根据需要调整）
                        )
            cbar = ax.collections[0].colorbar
            cbar.ax.tick_params(labelsize=14)

            # plt.title('相关系数热力图')
            # 单独设置坐标轴标签的字体大小
            plt.xticks(fontsize=14)
            plt.yticks(fontsize=14)



            plt.savefig('../../data/figure/d2d_delay_fit_corr.png',dpi=600,bbox_inches='tight')
            plt.savefig('../../data/figure/d2d_delay_fit_corr.svg')
            plt.savefig('../../data/figure/d2d_delay_fit_corr.pdf', bbox_inches='tight')
            plt.show()


            # X = data_preprocess.X.to_numpy()
            # Y = data_preprocess.y.to_numpy().reshape(-1, 1)
            # print(X.shape)
            # print(Y.shape)
            #
            # # 划分训练集与测试集
            # k = np.random.permutation(X.shape[0])
            # train_rate = 0.8
            # train_divide_num = math.floor(X.shape[0]*train_rate)
            # print('数据划分节点', train_divide_num)
            #
            # # 训练集
            # X_train = X[k[:train_divide_num], :]
            # Y_train = Y[k[:train_divide_num], :]
            #
            # # 测试集
            # X_test = X[k[train_divide_num:], :]
            # Y_test = Y[k[train_divide_num:], :]
            #
            # # 归一化
            # mms1 = preprocessing.MinMaxScaler()
            # X_train = mms1.fit_transform(X_train)  # mms会存下最大最小值用于反归一化
            # X_test = mms1.transform(X_test)
            #
            # mms2 = preprocessing.MinMaxScaler()
            # Y_train = mms2.fit_transform(Y_train)  # y不进行归一化的话，会导致误差非常大
            #
            # # 建立MLP模型
            # nn = MLPRegressor(hidden_layer_sizes=(8, 4),  # 隐含层
            #                   activation='relu',  # 激活函数
            #                   solver='sgd',  # 优化器
            #                   learning_rate='constant',  # 学习率的变化形式
            #                   learning_rate_init=0.0001,  # 学习率的值
            #                   batch_size=32,
            #                   max_iter=1000,  # 最大迭代次数
            #                   tol=0.00001)  # 允许误差
            #
            # # 训练MLP模型
            # nn.fit(X_train, Y_train.ravel())  # y_train.ravel() 将数据拉成一维数组
            #
            # # 保存模型
            # model_save_path = setting.model_save_path
            # model_save_path = model_save_path + 'ref_model' + \
            #                   time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime()) + '.pkl'
            # with open(model_save_path, 'wb') as f:
            #     pickle.dump(nn, f, protocol=pickle.HIGHEST_PROTOCOL)
            #
            #
            # # # MLP模型预测
            # y_sim = nn.predict(X_test)
            # Y_sim = mms2.inverse_transform(y_sim.reshape(-1, 1))  # 对预测结果进行反归一化
            #
            # # 计算相对误差
            # Error = np.abs(Y_sim-Y_test)/Y_test
            # Result = np.hstack((Y_sim, Y_test, Error))  # 拼接 真实值，预测值，相对误差
            # print(Result)
            #
            # # 绘制对比图
            # np_list = np.arange(Y_test.shape[0])
            # plt.plot(np_list, Y_sim, 'r*--', label='predict_value')
            # plt.plot(np_list, Y_test, 'bo-.', label='original_value')
            # plt.show()
            #
            #
            # # region 查看模型参数
            # print('输入层与隐含层之间的连接权值', nn.coefs_[0].shape)  # 输入层与隐含层之间的连接权值  401×200
            # print('隐含层与输出层之间的连接权值', nn.coefs_[1].shape)  # 隐含层与输出层之间的连接权值  200×1
            # print('隐含层神经元的个数', nn.intercepts_[0].shape)  # 隐含层神经元的阈值  # 隐含层阈值
            # print('输出层神经元的个数', nn.intercepts_[1].shape)  # 输出层神经元的阈值  # 输出层阈值
            #
            # print('模型损失', nn.loss_)
            # print('迭代次数', nn.n_iter_)
            # print('神经网络层数', nn.n_layers_)
            #
            #
            #
            # # 相关系数R^2
            # R = compute_correlation(Y_sim, Y_test)
            # print('相关系数R^2', R ** 2)
            #
            # # region 交叉验证(Cross Validation, CV)
            # scores = cross_val_score(nn, X_train, Y_train.ravel(), cv=5)  # 做交叉验证
            # print(scores)
            # print("Mean Score:", np.mean(scores), ", Max Score:", np.max(scores), ", Min Score:", np.min(scores))
            # # endregion
            #
            # # region 绘制迭代过程曲线
            # plt.plot(np.arange(nn.n_iter_), nn.loss_curve_)
            # plt.xlim((0, nn.n_iter_))
            # plt.xlabel("Iterative Epochs")
            # plt.ylabel("Loss")
            # plt.title("Iterative Curve")
            # plt.show()
            # # endregion
            #
            # # 模型应用
            # with open(model_save_path, 'rb') as f:
            #     loaded_model = pickle.load(f)
            #
            # mms3 = preprocessing.MinMaxScaler()
            # X_new = mms3.fit_transform(X)
            # y_sim_all = nn.predict(X_new)
            # # print(y_sim_all)
            # mms4 = preprocessing.MinMaxScaler()
            # mms4.fit_transform(Y)
            # Y_sim_all = mms4.inverse_transform(y_sim_all.reshape(-1, 1))
            # np_all_list = np.arange(Y.shape[0])
            # plt.plot(np_all_list, Y_sim_all, 'r*--', label='predict_value')
            # plt.plot(np_all_list, Y, 'bo-.', label='original_value')
            # plt.show()
            #
            # df_Y_pre = pd.DataFrame(Y_sim_all, columns=['pre_d2d_time'])
            # new_df = pd.concat([df, df_Y_pre], axis=1)
            # # 计算相关系数矩阵
            # print("Pandas相关系数矩阵:")
            # corr_pearson = new_df.corr(method='pearson')
            # print(new_df.corr(method='pearson'))  # 可选method: pearson/spearman/kendall
            #
            # seed_result.append((seed,corr_pearson.loc['d2d_time', 'pre_d2d_time']))
            # print('结果',(seed,corr_pearson.loc['d2d_time', 'pre_d2d_time']))

    # print(seed_result)








