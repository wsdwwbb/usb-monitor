"""
usb_monitor/cli.py — 命令行界面

列出所有 USB 设备，支持实时监控插拔。
"""

from __future__ import annotations

import argparse
import sys
import time

from usb_monitor.core import USBMonitor, enum_usb_devices, enum_usb_devices_fast


def print_devices(devices, verbose: bool = False):
    """格式化输出设备列表。"""
    if not devices:
        print("未检测到 USB 设备。")
        return

    print(f"\n{'='*60}")
    print(f"  共检测到 {len(devices)} 个 USB 设备")
    print(f"{'='*60}\n")

    for i, dev in enumerate(devices, 1):
        if verbose:
            print(f"#{i} {dev}")
        else:
            print(f"#{i:2d}  [{dev.status:6s}] {dev.name}")
            if dev.serial_number:
                print(f"     序列号: {dev.serial_number}")
            if dev.vendor_id and dev.product_id:
                print(f"     VID:PID: {dev.vendor_id}:{dev.product_id}")
        print()


def monitor_mode(poll_interval: float):
    """实时监控模式。"""
    print(f"正在监控 USB 设备插拔 (每 {poll_interval:.1f} 秒检测一次)...")
    print("按 Ctrl+C 退出\n")

    monitor = USBMonitor(poll_interval=poll_interval)

    def on_insert(dev):
        t = time.strftime("%H:%M:%S")
        print(f"[{t}] 🔌 插入: {dev.short_str()}")

    def on_remove(dev):
        t = time.strftime("%H:%M:%S")
        print(f"[{t}] 🔌 拔出: {dev.short_str()}")

    monitor.on_insert = on_insert
    monitor.on_remove = on_remove

    try:
        monitor.start()
    except KeyboardInterrupt:
        print("\n已停止监控。")
        monitor.stop()


def main():
    parser = argparse.ArgumentParser(
        description="USB Monitor — 检测电脑插入的 USB 设备",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  usb-monitor                 列出所有 USB 设备
  usb-monitor -v              显示详细信息
  usb-monitor --fast          使用 wmic (无需 pywin32)
  usb-monitor --monitor       实时监控设备插拔
  usb-monitor --monitor -i 1  每 1 秒检测一次
        """,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细信息（制造商、驱动版本等）",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="使用 wmic 命令模式（无需安装 pywin32）",
    )
    parser.add_argument(
        "-m", "--monitor",
        action="store_true",
        help="实时监控模式，检测设备插拔",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=2.0,
        help="监控模式下的轮询间隔（秒，默认 2.0）",
    )

    args = parser.parse_args()

    # 选择后端
    if args.fast:
        scan_func = enum_usb_devices_fast
    else:
        scan_func = enum_usb_devices

    try:
        if args.monitor:
            monitor_mode(args.interval)
        else:
            devices = scan_func()
            print_devices(devices, verbose=args.verbose)
    except ImportError as e:
        print(f"错误: {e}", file=sys.stderr)
        print("提示: 使用 --fast 参数可绕过此依赖", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()