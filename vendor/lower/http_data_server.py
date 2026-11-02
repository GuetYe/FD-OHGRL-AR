#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Identity information omitted for anonymous peer review.
Identity information omitted for anonymous peer review.
@Time:2025/4/3 22:16
@File:http_data_server.py
@Desc:****************
"""
from flask import Flask,request,jsonify
import json

app = Flask(__name__)

@app.route('/')
def home():
    return "Welcome to API Data Server! Use /api/state or /api/action"


@app.route('/api/state',methods=['GET','POST'])
def upload_state():
    transit_folder = '../../api/data/trans_data.json'
    # received_data = request.json
    # print("Up_state:", received_data)
    # return jsonify(received_data)
    # received_data = None
    if request.method == 'POST':
        received_data = request.json
        print("Up_state:", received_data)
        try:
            with open(transit_folder,'w',encoding='utf-8') as f:
                json.dump(received_data, f, ensure_ascii=False, indent=4)
            return jsonify(received_data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No file part'}, 400
    if request.method == 'GET':
        try:
            with open(transit_folder,'r',encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No selected file'}, 400

@app.route('/api/request',methods=['GET','POST'])
def upload_request():
    transit_folder = '../../api/data/request_data.json'
    # received_data = request.json
    # print("Up_state:", received_data)
    # return jsonify(received_data)
    # received_data = None
    if request.method == 'POST':
        received_data = request.json
        print("Request:", received_data)
        try:
            with open(transit_folder,'w',encoding='utf-8') as f:
                json.dump(received_data, f, ensure_ascii=False, indent=4)
            return jsonify(received_data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No file part'}, 400
    if request.method == 'GET':
        try:
            with open(transit_folder,'r',encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No selected file'}, 400


@app.route('/api/action',methods=['POST','GET'])
def upload_action():
    transit_folder = '../../api/data/action_data.json'
    # received_data = request.json
    # print("Up_state:", received_data)
    # return jsonify(received_data)
    # received_data = None
    if request.method == 'POST':
        received_data = request.json
        print("Control:", received_data)
        try:
            with open(transit_folder, 'w', encoding='utf-8') as f:
                json.dump(received_data, f, ensure_ascii=False, indent=4)
            return jsonify(received_data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No file part'}, 400
    if request.method == 'GET':
        try:
            with open(transit_folder, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception as e:
            print(e)
            return {'status': 'error', 'message': 'No selected file'}, 400

def _clear():
    empty_json = {}

    for file in ['../../api/data/trans_data.json',
                 '../../api/data/request_data.json',
                 '../../api/data/action_data.json']:
        try:
            with open(file, 'w', encoding='utf-8') as f:
                json.dump(empty_json, f, ensure_ascii=False, indent=4)
            print(f"[Init] Cleared file: {file}")
        except Exception as e:
            print(f"[Init Error] Could not clear file {file}: {e}")

if __name__ == '__main__':
    _clear()
    app.run(host='0.0.0.0',   # 监听所有网络接口（包括公网和局域网）
            port=5000,   # 使用5000端口（Flask默认端口）
            debug=False  # 禁用调试模式（生产环境必须关闭）
            )  # 关闭debug模式避免安全风险

    # 如果debug=True会开启以下危险功能
    # 1.允许执行任意代码的调试器（PIN码可配）
