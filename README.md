# USB Monitor — 电脑 USB 设备检测工具

一个简单易用的 Windows 工具，用来查看电脑上插入了哪些 USB 设备。

## 功能

- 🔍 **列出所有 USB 设备** — 名称、状态、VID:PID、序列号、类别
- 📋 **设备详情** — 制造厂商、驱动版本、PNP ID 等
- 👀 **实时监控** — 检测 USB 设备的插入和拔出
- 🖥️ **图形界面** — 基于 tkinter，直观易用
- ⌨️ **命令行模式** — 适合脚本和远程使用

## 直接运行（无需安装 Python）

```bash
双击 dist/USBMonitor.exe 即可打开图形界面
```

或在命令行中：
```bash
dist\USBMonitor.exe                  # 图形界面
dist\USBMonitor.exe --cli            # 命令行模式
dist\USBMonitor.exe --cli -v         # 详细模式
dist\USBMonitor.exe --cli --monitor  # 实时监控
```

## 通过 Python 源码运行

### 安装

```bash
# 1. 安装依赖（推荐）
pip install pywin32

# 2. 直接运行
python -m usb_monitor
```

> 不安装 `pywin32` 也能使用，工具会自动降级为 `wmic` 命令模式（功能基本一致）。

## 使用方法

### 图形界面（默认）

```bash
python -m usb_monitor
```

### 命令行模式

```bash
# 列出所有 USB 设备
python -m usb_monitor --cli

# 显示详细信息
python -m usb_monitor --cli -v

# 使用 wmic 模式（无需 pywin32）
python -m usb_monitor --cli --fast

# 实时监控插拔
python -m usb_monitor --cli --monitor
```

### 命令行帮助

```bash
python -m usb_monitor --cli -h
```

## 截图预览

| 图形界面 | 命令行 |
|---------|--------|
| 树状列表显示所有 USB 设备，点击查看详情 | 格式化输出设备列表 |
| 实时监控模式下插入/拔出设备有动画提示 | 支持 `--monitor` 实时监控 |

## 技术原理

基于 Windows WMI（Windows Management Instrumentation）查询 `Win32_PnPEntity` 类，
筛选 PNPClass 为 USB / HID 的设备，或 DeviceID 以 `USB\` 开头的设备。

- **推荐后端**: `pywin32` — 直接 COM 调用 WMI，速度快
- **备选后端**: `wmic` — 命令行模式，无需额外安装