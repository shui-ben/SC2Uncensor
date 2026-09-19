# -*- coding: utf-8 -*-
"""打印 config.json 的 admin 选项(True/False), 供 sc2_uncensor.bat 决定是否弹UAC提权。
独立成文件是为了让 bat 里的 for /f 命令不含括号/嵌套引号(batch 解析高危写法)。"""
import json
import os

try:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    cfg = json.load(open(path, encoding="utf-8-sig"))   # 容忍记事本存的 BOM
    print("True" if cfg.get("admin") else "False")
except Exception:
    print("False")
