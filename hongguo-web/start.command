#!/bin/zsh
set -e
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ ! -x .venv/bin/python ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv .venv
  elif command -v python3.11 >/dev/null 2>&1; then
    python3.11 -m venv .venv
  else
    echo "需要 Python 3.11 或 3.12。请先安装后重新打开 start.command。"
    read -k 1 '?按任意键关闭…'
    exit 1
  fi
fi

if [[ ! -e .venv/.hongguo-dependencies-ready ]]; then
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.hongguo-dependencies-ready
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "提示：未在 PATH 找到 ffmpeg/ffprobe。搜索可用，下载前请安装 FFmpeg。"
fi

echo "即将启动本机 Web 服务。关闭此窗口会停止服务。"
exec .venv/bin/python server.py
