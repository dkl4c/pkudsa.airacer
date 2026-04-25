"""
config.py — 本机配置（从本文件复制，不提交到 git）

所有路径均从项目根目录自动推导，通常不需要修改。
需要机器差异的设置通过环境变量覆盖：

  WEBOTS_BINARY          Webots 可执行文件路径
  AIRACER_ADMIN_PASSWORD 管理员密码
  AIRACER_HOST           服务器监听地址（默认 0.0.0.0）
  AIRACER_PORT           服务器端口（默认 8000）

环境变量示例（PowerShell）：
  $env:WEBOTS_BINARY = "D:\\Webots\\msys64\\mingw64\\bin\\webotsw.exe"
  $env:AIRACER_ADMIN_PASSWORD = "your_password"

或在系统环境变量中永久设置，无需每次手动指定。
"""

import os
import pathlib

# Project root is one level above this file (server/)
_ROOT = pathlib.Path(__file__).resolve().parent.parent

AIRACER_ROOT     = str(_ROOT)
RACE_CONFIG_PATH = str(_ROOT / "race_config.json")
RECORDINGS_DIR   = str(_ROOT / "recordings")
SUBMISSIONS_DIR  = str(_ROOT / "submissions")
WORLD_FILE       = str(_ROOT / "webots" / "worlds" / "airacer.wbt")
DB_PATH          = str(_ROOT / "server" / "db" / "race.db")

WEBOTS_BINARY = os.environ.get(
    "WEBOTS_BINARY",
    r"D:\Webots\msys64\mingw64\bin\webotsw.exe",  # 改为实际路径或设置环境变量
)

ADMIN_PASSWORD = os.environ.get("AIRACER_ADMIN_PASSWORD", "12345") # 默认密码，建议修改为更安全的密码并通过环境变量覆盖

SERVER_HOST = os.environ.get("AIRACER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("AIRACER_PORT", "8000"))
