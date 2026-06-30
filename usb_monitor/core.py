"""
usb_monitor/core.py — USB 设备检测核心模块

基于 Windows WMI (Win32_PnPEntity) 枚举所有 USB 设备，
并支持监听设备插拔事件。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class USBDevice:
    """表示一个 USB 设备的信息。"""
    name: str                       # 设备名称
    description: str = ""           # 设备描述
    status: str = ""                # 状态 (OK / Error / Unknown)
    pnp_device_id: str = ""         # PNP 设备 ID
    vendor_id: str = ""             # 供应商 ID (VID)
    product_id: str = ""            # 产品 ID (PID)
    serial_number: str = ""         # 序列号 (如果有)
    device_class: str = ""          # 设备类别 (USB / HID / Storage 等)
    driver_version: str = ""        # 驱动版本
    manufacturer: str = ""          # 制造商
    service: str = ""               # 服务名
    caption: str = ""               # 设备标题

    def __str__(self) -> str:
        return (
            f"[{self.status}] {self.name}\n"
            f"  ├─ 描述:     {self.description or 'N/A'}\n"
            f"  ├─ 制造商:   {self.manufacturer or 'N/A'}\n"
            f"  ├─ VID:PID:  {self.vendor_id}:{self.product_id}\n"
            f"  ├─ 序列号:   {self.serial_number or 'N/A'}\n"
            f"  ├─ 类别:     {self.device_class or 'N/A'}\n"
            f"  ├─ 驱动:     {self.driver_version or 'N/A'}\n"
            f"  └─ PNP ID:   {self.pnp_device_id}"
        )

    def short_str(self) -> str:
        """精简的单行描述。"""
        serial = f" (SN: {self.serial_number})" if self.serial_number else ""
        return f"{self.name}{serial} — {self.vendor_id}:{self.product_id}"

    @property
    def is_composite_parent(self) -> bool:
        """判断是否为复合设备的父节点（PNP ID 中不含 &MI_ 且 VID/PID 齐全）。"""
        if not self.vendor_id or not self.product_id:
            return False
        pnp = self.pnp_device_id.upper()
        # 父节点形如 USB\VID_xxxx&PID_xxxx\INSTANCE
        # 子接口形如 USB\VID_xxxx&PID_xxxx&MI_XX\INSTANCE
        # wmic 可能输出 &amp;MI_XX，两种情况都匹配
        return "&MI_" not in pnp and "&AMP;MI_" not in pnp

    @property
    def group_key(self) -> str:
        """
        生成设备分组标识，同一物理设备的多个接口共享同一 key。

        策略（按优先级）:
          1. 有真实序列号 → VID:PID + 序列号（最精确，区分同型号多设备）
          2. 仅 VID:PID → VID:PID 直接分组（同一设备的不同接口合并）
          3. 兜底 → 设备名称
        """
        if not self.vendor_id or not self.product_id:
            return f"__ungrouped__|{self.name}"

        base = f"{self.vendor_id}:{self.product_id}"

        # 检测是否为真实 USB 序列号（不含 & 符号，非 Windows 实例 ID）
        sn = self.serial_number
        if sn and "&" not in sn:
            return f"{base}|SN:{sn}"

        # 无真实序列号 → 按 VID:PID 归为一组
        return base


# ---------------------------------------------------------------------------
# WMI 适配器 — 分离导入错误与运行逻辑
# ---------------------------------------------------------------------------

def _extract_vid_pnpid(pnp_id: str):
    """从 PNP Device ID 中解析 VID 和 PID，如 'USB\\VID_1234&PID_5678\\...'"""
    m = re.search(r'VID_([0-9A-Fa-f]{4})', pnp_id)
    vid = m.group(1).upper() if m else ""
    m = re.search(r'PID_([0-9A-Fa-f]{4})', pnp_id)
    pid = m.group(1).upper() if m else ""
    return vid, pid


def _extract_serial(pnp_id: str):
    """从 PNP Device ID 中提取序列号（最后一段）。"""
    parts = pnp_id.split("\\")
    if len(parts) >= 3 and parts[-1] and parts[-1] != "0":
        return parts[-1]
    # 也检查 parent 段
    if len(parts) >= 3:
        parent = parts[-2]
        m = re.search(r'[A-F0-9]{8,}', parent)
        if m:
            return m.group(0)
    return ""


def enum_usb_devices() -> List[USBDevice]:
    """
    通过 WMI 枚举所有 USB 设备（Win32_PnPEntity 中 PNPClass 含 USB 的条目）。
    返回 USBDevice 列表。
    """
    devices: List[USBDevice] = []
    try:
        import win32com.client
    except ImportError:
        raise ImportError(
            "需要 pywin32 库。请运行: pip install pywin32"
        )

    try:
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = wmi.ConnectServer(".", "root\\cimv2")
        # 获取所有 PnP 设备，然后过滤 USB 相关
        col = svc.ExecQuery(
            "SELECT * FROM Win32_PnPEntity "
            "WHERE PNPClass LIKE '%USB%' OR PNPClass LIKE '%HID%' "
            "OR DeviceID LIKE 'USB\\\\%'"
        )
    except Exception as e:
        raise RuntimeError(f"无法连接 WMI: {e}")

    for item in col:
        try:
            pnp_id = getattr(item, "PNPDeviceID", "") or ""
            name = getattr(item, "Name", "") or ""
            desc = getattr(item, "Description", "") or ""
            status = getattr(item, "Status", "") or "Unknown"

            vid, pid = _extract_vid_pnpid(pnp_id)
            serial = _extract_serial(pnp_id)
            device_class = getattr(item, "PNPClass", "") or ""
            caption = getattr(item, "Caption", "") or ""
            manufacturer = getattr(item, "Manufacturer", "") or ""
            service = getattr(item, "Service", "") or ""

            # 尝试获取驱动版本
            driver_version = ""
            try:
                drv_col = svc.ExecQuery(
                    f"SELECT DriverVersion FROM Win32_PnPSignedDriver "
                    f"WHERE DeviceID = '{pnp_id.replace(chr(39), chr(39)*2)}'"
                )
                for drv in drv_col:
                    dv = getattr(drv, "DriverVersion", "")
                    if dv:
                        driver_version = dv
                        break
            except Exception:
                pass

            device = USBDevice(
                name=name or desc or caption,
                description=desc,
                status=status,
                pnp_device_id=pnp_id,
                vendor_id=vid,
                product_id=pid,
                serial_number=serial,
                device_class=device_class,
                driver_version=driver_version,
                manufacturer=manufacturer,
                service=service,
                caption=caption,
            )
            devices.append(device)
        except Exception:
            # 跳过无法读取的设备
            continue

    return devices


# ---------------------------------------------------------------------------
# 快速模式：使用 WMI 命令行工具 (更轻量，无需 pywin32)
# ---------------------------------------------------------------------------

def enum_usb_devices_fast() -> List[USBDevice]:
    """
    使用 wmic 命令行工具枚举 USB 设备。
    不需要 pywin32，但速度稍慢。
    """
    import subprocess
    import json

    devices: List[USBDevice] = []

    try:
        # wmic 输出为 csv 格式以便解析
        result = subprocess.run(
            [
                "wmic", "path", "Win32_PnPEntity",
                "where", "PNPClass like '%%USB%%' or PNPClass like '%%HID%%'",
                "get",
                "PNPDeviceID,Name,Description,Status,PNPClass,Manufacturer,Service,Caption",
                "/format:csv",
            ],
            capture_output=True, text=True, timeout=10,
        )
        # wmic CSV 会将 & 转义为 &amp;，需要还原
        raw = result.stdout.replace("&amp;", "&")
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        if len(lines) < 2:
            return devices

        headers = [h.strip() for h in lines[0].split(",")]
        for line in lines[1:]:
            cols = line.split(",")
            if len(cols) < len(headers):
                continue
            row = dict(zip(headers, cols))
            pnp_id = row.get("PNPDeviceID", "")
            if not pnp_id:
                continue

            vid, pid = _extract_vid_pnpid(pnp_id)
            serial = _extract_serial(pnp_id)
            name = row.get("Name", "") or ""
            desc = row.get("Description", "") or ""

            devices.append(USBDevice(
                name=name or desc,
                description=desc,
                status=row.get("Status", "Unknown"),
                pnp_device_id=pnp_id,
                vendor_id=vid,
                product_id=pid,
                serial_number=serial,
                device_class=row.get("PNPClass", ""),
                manufacturer=row.get("Manufacturer", ""),
                service=row.get("Service", ""),
                caption=row.get("Caption", ""),
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return devices


# ---------------------------------------------------------------------------
# 设备插拔检测
# ---------------------------------------------------------------------------

class USBMonitor:
    """
    USB 设备插拔监听器。

    用法:
        monitor = USBMonitor()
        monitor.on_insert = lambda dev: print(f"插入: {dev.name}")
        monitor.on_remove = lambda dev: print(f"拔出: {dev.name}")
        monitor.start()   # 开始轮询 (每 2 秒)
        ...
        monitor.stop()
    """

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self.on_insert: Optional[Callable[[USBDevice], None]] = None
        self.on_remove: Optional[Callable[[USBDevice], None]] = None
        self._known: dict[str, USBDevice] = {}
        self._running = False

    def start(self):
        """开始轮询检测设备插拔。"""
        self._known = {d.pnp_device_id: d for d in enum_usb_devices()}
        self._running = True
        while self._running:
            time.sleep(self.poll_interval)
            try:
                current = enum_usb_devices()
            except Exception:
                continue
            current_map = {d.pnp_device_id: d for d in current}

            # 新插入的设备
            for pid, dev in current_map.items():
                if pid not in self._known:
                    if self.on_insert:
                        self.on_insert(dev)

            # 被拔出的设备
            for pid, dev in self._known.items():
                if pid not in current_map:
                    if self.on_remove:
                        self.on_remove(dev)

            self._known = current_map

    def stop(self):
        """停止轮询。"""
        self._running = False

    def scan_once(self) -> List[USBDevice]:
        """手动扫描一次。"""
        devs = enum_usb_devices()
        self._known = {d.pnp_device_id: d for d in devs}
        return devs


# ---------------------------------------------------------------------------
# 设备分组
# ---------------------------------------------------------------------------

@dataclass
class DeviceGroup:
    """一组属于同一物理 USB 设备的关联接口。"""
    key: str                          # group_key
    parent: Optional[USBDevice] = None  # 复合设备父节点
    children: List[USBDevice] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """供 UI 显示的分组名称。"""
        if self.parent:
            name = self.parent.name
            vidpid = f"{self.parent.vendor_id}:{self.parent.product_id}"
            return f"{name}  [{vidpid}]"
        if self.children:
            c = self.children[0]
            vidpid = f"{c.vendor_id}:{c.product_id}" if c.vendor_id else ""
            return f"USB 设备  [{vidpid}]" if vidpid else "USB 设备"
        return "未知设备"

    def all_devices(self) -> List[USBDevice]:
        """组内所有设备（父节点 + 子接口）。"""
        result = []
        if self.parent:
            result.append(self.parent)
        result.extend(self.children)
        return result


def group_devices(devices: List[USBDevice]) -> List[DeviceGroup]:
    """
    按物理设备对 USB 设备列表进行分组。
    同一 VID:PID + 实例路径的设备归为一组。
    """
    groups: dict[str, DeviceGroup] = {}

    for dev in devices:
        key = dev.group_key

        if key not in groups:
            groups[key] = DeviceGroup(key=key)

        if dev.is_composite_parent:
            groups[key].parent = dev
        else:
            groups[key].children.append(dev)

    # 对 children 排序：MI_00, MI_01 ...
    for g in groups.values():
        g.children.sort(key=lambda d: d.pnp_device_id)

    # 按父节点名称排序输出
    result = sorted(groups.values(), key=lambda g: g.display_name.lower())
    return result


# ---------------------------------------------------------------------------
# 幽灵 COM 口检测与清理
# ---------------------------------------------------------------------------

@dataclass
class GhostComPort:
    """一个幽灵 COM 口的信息。"""
    port: str           # COM 编号，如 "COM12"
    vid_pid: str        # VID 和 PID，如 "VID_0D28&PID_0204"
    instance: str       # 实例标识
    device_name: str = ""  # 设备名称（如能获取到）

    @property
    def is_active(self) -> bool:
        """是否当前有物理设备占用此 COM 口。"""
        return False  # 幽灵口意味着不在活动列表中


def scan_ghost_com_ports() -> List[GhostComPort]:
    """
    扫描幽灵 COM 口：
    对比注册表中所有 USB COM 口分配记录 与 当前活动 COM 口列表，
    返回没有实际设备连接但仍被占用的 COM 口。
    
    需要管理员权限才能读取完整的注册表信息。
    """
    import winreg
    
    active_ports: set[str] = set()
    ghost_ports: List[GhostComPort] = []

    # 1. 获取当前活动的 COM 口
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            _, value, _ = winreg.EnumValue(key, i)
            active_ports.add(str(value).upper())
            i += 1
    except WindowsError:
        pass
    finally:
        winreg.CloseKey(key)

    # 2. 扫描 USB 枚举中所有带 COM 口的设备记录
    try:
        usb_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SYSTEM\CurrentControlSet\Enum\USB")
    except Exception as e:
        raise RuntimeError(f"无法打开注册表（可能需要管理员权限）: {e}")

    i = 0
    while True:
        try:
            vid_pid = winreg.EnumKey(usb_key, i)
        except WindowsError:
            break  # 枚举完毕
        i += 1

        if "VID_" not in vid_pid:
            continue
        try:
            inst_key = winreg.OpenKey(usb_key, vid_pid)
        except Exception:
            continue
        j = 0
        while True:
            try:
                inst = winreg.EnumKey(inst_key, j)
            except WindowsError:
                break
            j += 1
            try:
                params = winreg.OpenKey(inst_key,
                                        inst + r"\Device Parameters")
                port_name, _ = winreg.QueryValueEx(params, "PortName")
                winreg.CloseKey(params)

                port_upper = str(port_name).upper()
                dev_name = ""
                try:
                    dev_key = winreg.OpenKey(inst_key, inst)
                    try:
                        ff, _ = winreg.QueryValueEx(dev_key, "FriendlyName")
                        dev_name = str(ff)
                    except Exception:
                        pass
                    try:
                        dd, _ = winreg.QueryValueEx(dev_key, "DeviceDesc")
                        dev_name = dev_name or str(dd)
                    except Exception:
                        pass
                    winreg.CloseKey(dev_key)
                except Exception:
                    pass

                if port_upper not in active_ports:
                    ghost_ports.append(GhostComPort(
                        port=str(port_name),
                        vid_pid=vid_pid,
                        instance=inst,
                        device_name=dev_name,
                    ))
            except Exception:
                pass
        winreg.CloseKey(inst_key)
    winreg.CloseKey(usb_key)

    # 按 COM 编号排序
    def _com_sort_key(p: GhostComPort):
        num = p.port[3:]  # "COM12" -> "12"
        try:
            return int(num)
        except ValueError:
            return 999

    ghost_ports.sort(key=_com_sort_key)
    return ghost_ports


def cleanup_ghost_com_port(port: GhostComPort) -> bool:
    """
    删除一个幽灵 COM 口的注册表记录。
    需要管理员权限。
    """
    import subprocess
    import winreg

    # 方法1: 使用 pnputil (Windows 10/11 内置)
    instance_id = f"USB\\{port.vid_pid}\\{port.instance}"
    try:
        result = subprocess.run(
            ["pnputil", "/remove-device", instance_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 方法2: 使用 devcon (如果有)
    try:
        result = subprocess.run(
            ["devcon", "remove", instance_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 方法3: 直接删除注册表键 (需要管理员权限)
    try:
        key_path = f"SYSTEM\\CurrentControlSet\\Enum\\USB\\{port.vid_pid}\\{port.instance}"
        # 先尝试删除子键
        def _del_key(root, path):
            try:
                k = winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS)
                # 删除所有子键
                while True:
                    try:
                        sub_name = winreg.EnumKey(k, 0)
                        _del_key(k, sub_name)
                    except WindowsError:
                        break
                winreg.CloseKey(k)
                winreg.DeleteKey(root, path)
                return True
            except Exception:
                return False

        return _del_key(winreg.HKEY_LOCAL_MACHINE, key_path)
    except Exception:
        pass

    return False


def cleanup_all_ghost_com_ports(ports: List[GhostComPort]) -> dict[str, bool]:
    """
    批量清理幽灵 COM 口。
    返回 {COM口: 是否成功} 的字典。
    """
    results = {}
    for port in ports:
        results[port.port] = cleanup_ghost_com_port(port)
    return results
