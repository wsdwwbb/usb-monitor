#!/usr/bin/env python3
"""
USB Monitor — 检测电脑插入的 USB 设备

用法:
  python -m usb_monitor         图形界面
  python -m usb_monitor --cli   命令行模式
  python -m usb_monitor -h      查看帮助
"""

from __future__ import annotations

import sys


def main():
    # 如果传了 --cli 则启动命令行，否则启动 GUI
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from usb_monitor.cli import main as cli_main
        cli_main()
    else:
        try:
            from usb_monitor.gui import main as gui_main
            gui_main()
        except ImportError:
            print("GUI 启动失败，切换到命令行模式...", file=sys.stderr)
            from usb_monitor.cli import main as cli_main
            cli_main()


if __name__ == "__main__":
    main()