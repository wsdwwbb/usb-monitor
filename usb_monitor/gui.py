"""
usb_monitor/gui.py — 图形界面 (tkinter)

显示 USB 设备列表（支持按物理设备分组），
支持一键刷新和实时监控。
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

from usb_monitor.core import USBDevice, DeviceGroup, group_devices, GhostComPort
from usb_monitor.core import enum_usb_devices, enum_usb_devices_fast, scan_ghost_com_ports


try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    tk = None  # type: ignore


# ── 颜色常量 ──────────────────────────────────────────────────────────────
COLOR_OK = "#2e7d32"
COLOR_ERROR = "#c62828"
COLOR_UNKNOWN = "#666666"
COLOR_INSERT = "#e8f5e9"
COLOR_REMOVE = "#ffebee"
COLOR_GROUP_BG = "#f5f5f5"

# ── 内部 iid 前缀 ─────────────────────────────────────────────────────────
GROUP_PREFIX = "__group__"


def _group_iid(key: str) -> str:
    return f"{GROUP_PREFIX}{key}"


class USBGUI:
    """USB 设备检测图形界面。"""

    def __init__(self):
        if tk is None:
            raise ImportError("tkinter 是 Python 内置模块，不应缺失。请检查 Python 安装。")

        self.root = tk.Tk()
        self.root.title("USB Monitor — USB 设备检测工具")
        self.root.geometry("860x500+100+100")
        self.root.minsize(680, 400)

        self._monitor_active = False
        self._grouping = True                     # 默认启用分组
        self._devices: Dict[str, USBDevice] = {}  # pnp_device_id -> USBDevice
        self._device_groups: Dict[str, DeviceGroup] = {}  # group_key -> DeviceGroup

        self._build_ui()
        self.refresh()

    # ── UI 构建 ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # ---- 顶部工具栏 ----
        toolbar = ttk.Frame(self.root, padding="6 6 6 2")
        toolbar.pack(fill=tk.X)

        ttk.Label(toolbar, text="USB 设备检测工具",
                  font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=(0, 20))

        self.btn_refresh = ttk.Button(
            toolbar, text="🔄 刷新", command=self.refresh
        )
        self.btn_refresh.pack(side=tk.LEFT, padx=4)

        self.btn_monitor = ttk.Button(
            toolbar, text="▶ 实时监控", command=self.toggle_monitor
        )
        self.btn_monitor.pack(side=tk.LEFT, padx=4)

        self.btn_cleanup = ttk.Button(
            toolbar, text="🧹 清理 COM 口", command=self._cleanup_com_ports
        )
        self.btn_cleanup.pack(side=tk.LEFT, padx=4)

        self.cb_group = ttk.Checkbutton(
            toolbar, text="按设备分组",
            variable=None,
            command=self._toggle_grouping,
        )
        self.cb_group.state(("selected",))

        self.lbl_count = ttk.Label(toolbar, text="")
        self.lbl_count.pack(side=tk.RIGHT, padx=10)

        # ---- 设备树状列表 ----
        container = ttk.Frame(self.root, padding="6 2 6 6")
        container.pack(fill=tk.BOTH, expand=True)

        columns = ("status", "name", "vid_pid", "serial", "class")
        self.tree = ttk.Treeview(
            container, columns=columns, show="tree headings",
            selectmode="browse",
        )

        self.tree.heading("#0", text="", anchor=tk.W)
        self.tree.heading("status", text="状态", anchor=tk.W)
        self.tree.heading("name", text="设备名称", anchor=tk.W)
        self.tree.heading("vid_pid", text="VID:PID", anchor=tk.W)
        self.tree.heading("serial", text="序列号", anchor=tk.W)
        self.tree.heading("class", text="类别", anchor=tk.W)

        self.tree.column("#0", width=30, stretch=False)
        self.tree.column("status", width=80, stretch=False)
        self.tree.column("name", width=280, minwidth=120)
        self.tree.column("vid_pid", width=110, stretch=False)
        self.tree.column("serial", width=160)
        self.tree.column("class", width=80, stretch=False)

        vscroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # tag 样式
        self.tree.tag_configure("OK", foreground=COLOR_OK)
        self.tree.tag_configure("Error", foreground=COLOR_ERROR)
        self.tree.tag_configure("Unknown", foreground=COLOR_UNKNOWN)
        self.tree.tag_configure("group_header", font=("Segoe UI", 9, "bold"),
                                background=COLOR_GROUP_BG)
        self.tree.tag_configure("insert_anim", background=COLOR_INSERT)
        self.tree.tag_configure("remove_anim", background=COLOR_REMOVE)

        # ---- 底部详情面板 ----
        self.detail_frame = ttk.LabelFrame(
            self.root, text="设备详情", padding="6 4 6 6"
        )
        self.detail_frame.pack(fill=tk.X, padx=6, pady=(0, 6))

        self.lbl_detail = ttk.Label(
            self.detail_frame,
            text="选择一个设备查看详情",
            font=("Consolas", 9),
            foreground="#888",
        )
        self.lbl_detail.pack(fill=tk.X)

    # ── 分组开关 ──────────────────────────────────────────────────────────

    def _toggle_grouping(self):
        self._grouping = not self._grouping
        self.cb_group.state(("!selected",) if not self._grouping else ("selected",))
        # 用当前数据重建树
        all_devices = list(self._devices.values())
        self._populate_tree(all_devices)

    # ── 数据加载 ──────────────────────────────────────────────────────────

    def refresh(self):
        """刷新设备列表。"""
        # 清除所有高亮
        for item in self.tree.get_children():
            tags = self.tree.item(item, "tags")
            tags = tuple(t for t in tags if t not in ("insert_anim", "remove_anim"))
            self.tree.item(item, tags=tags)

        try:
            devices = enum_usb_devices()
        except ImportError:
            messagebox.showwarning(
                "依赖缺失",
                "未安装 pywin32，将使用 wmic 模式（功能受限）。\n"
                "建议: pip install pywin32",
            )
            try:
                devices = enum_usb_devices_fast()
            except Exception as e:
                messagebox.showerror("错误", f"无法检测 USB 设备: {e}")
                return
        except Exception as e:
            messagebox.showerror("错误", f"无法检测 USB 设备: {e}")
            return

        self._store_devices(devices)
        self._populate_tree(devices)

    def _store_devices(self, devices: List[USBDevice]):
        self._devices = {d.pnp_device_id: d for d in devices}
        groups = group_devices(devices)
        self._device_groups = {g.key: g for g in groups}
        device_count = len(devices)
        group_count = len(groups)

        if self._grouping:
            self.lbl_count.config(
                text=f"{device_count} 个设备 · {group_count} 组"
            )
        else:
            self.lbl_count.config(text=f"共 {device_count} 个设备")

    # ── 树填充 ────────────────────────────────────────────────────────────

    def _populate_tree(self, devices: List[USBDevice]):
        """填充树状列表。"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not devices:
            return

        if self._grouping:
            self._populate_grouped(devices)
        else:
            self._populate_flat(devices)

    def _populate_flat(self, devices: List[USBDevice]):
        """平铺模式：设备直接挂在根节点下。"""
        def sort_key(d: USBDevice):
            return (0 if d.status == "OK" else 1, d.name.lower())

        for dev in sorted(devices, key=sort_key):
            status_tag = ("OK" if dev.status == "OK"
                          else "Error" if dev.status in ("Error", "Degraded")
                          else "Unknown")
            self.tree.insert(
                "", tk.END,
                iid=dev.pnp_device_id,
                values=(
                    dev.status,
                    dev.name,
                    f"{dev.vendor_id}:{dev.product_id}" if dev.vendor_id else "-",
                    dev.serial_number or "-",
                    dev.device_class or "-",
                ),
                tags=(status_tag,),
            )

    def _populate_grouped(self, devices: List[USBDevice]):
        """分组模式：同一物理设备归为一个可折叠组。"""
        groups = group_devices(devices)

        for g in sorted(groups, key=lambda g: g.display_name.lower()):
            gid = _group_iid(g.key)

            # 展开的子设备数
            total_in_group = len(g.children)
            if g.parent:
                total_in_group += 1

            self.tree.insert(
                "", tk.END,
                iid=gid,
                text=f"  ({total_in_group} 个接口)",
                values=(
                    g.parent.status if g.parent else "",
                    g.display_name,
                    "",
                    "",
                    "",
                ),
                tags=("group_header",),
                open=True,
            )

            # 父设备作为第一个子节点
            if g.parent:
                p = g.parent
                status_tag = ("OK" if p.status == "OK"
                              else "Error" if p.status in ("Error", "Degraded")
                              else "Unknown")
                self.tree.insert(
                    gid, tk.END,
                    iid=p.pnp_device_id,
                    values=(
                        p.status,
                        p.name,
                        f"{p.vendor_id}:{p.product_id}" if p.vendor_id else "-",
                        p.serial_number or "-",
                        p.device_class or "-",
                    ),
                    tags=(status_tag,),
                )

            # 子接口
            for child in g.children:
                status_tag = ("OK" if child.status == "OK"
                              else "Error" if child.status in ("Error", "Degraded")
                              else "Unknown")
                self.tree.insert(
                    gid, tk.END,
                    iid=child.pnp_device_id,
                    values=(
                        child.status,
                        child.name,
                        f"{child.vendor_id}:{child.product_id}" if child.vendor_id else "-",
                        child.serial_number or "-",
                        child.device_class or "-",
                    ),
                    tags=(status_tag,),
                )

    # ── 窗口居中辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _center_window(win: tk.Toplevel, width: int, height: int):
        """将子窗口居中于主窗口显示。"""
        win.update_idletasks()
        try:
            px = win.master.winfo_x()
            py = win.master.winfo_y()
            pw = win.master.winfo_width()
            ph = win.master.winfo_height()
            x = px + (pw - width) // 2
            y = py + (ph - height) // 2
        except Exception:
            x = (win.winfo_screenwidth() - width) // 2
            y = (win.winfo_screenheight() - height) // 2
        win.geometry(f"{width}x{height}+{x}+{y}")

    # ── 幽灵 COM 口清理 ──────────────────────────────────────────────────

    def _cleanup_com_ports(self):
        """扫描并清理幽灵 COM 口。"""
        # 扫描
        try:
            ghosts = scan_ghost_com_ports()
        except Exception as e:
            messagebox.showerror("错误", f"扫描失败: {e}")
            return

        if not ghosts:
            messagebox.showinfo("清理 COM 口", "没有发现幽灵 COM 口，所有 COM 编号均有设备占用。")
            return

        # 弹出选择对话框
        top = tk.Toplevel(self.root)
        top.title("幽灵 COM 口清理")
        top.transient(self.root)
        top.grab_set()
        # 居中显示于主窗口
        self._center_window(top, 550, 380)

        ttk.Label(top, text=f"发现 {len(ghosts)} 个幽灵 COM 口（设备已拔出、COM 编号被残留占用）",
                  font=("", 10)).pack(pady=(10, 5))

        frame = ttk.Frame(top, padding="6")
        frame.pack(fill=tk.BOTH, expand=True)

        # 列表
        columns = ("port", "device", "vid_pid")
        tree = ttk.Treeview(frame, columns=columns, show="headings",
                            height=10, selectmode="extended")
        tree.heading("port", text="COM 口")
        tree.heading("device", text="设备名称")
        tree.heading("vid_pid", text="VID:PID")
        tree.column("port", width=60)
        tree.column("device", width=260)
        tree.column("vid_pid", width=180)

        vscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vscroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        for g in ghosts:
            tree.insert("", tk.END, iid=g.port, values=(
                g.port,
                g.device_name[:60] if g.device_name else "(未知设备)",
                g.vid_pid,
            ))

        # 全选 / 反选
        sel_frame = ttk.Frame(top, padding="6 0 6 6")
        sel_frame.pack(fill=tk.X)

        def select_all():
            for item in tree.get_children():
                tree.selection_add(item)

        def deselect_all():
            for item in tree.get_children():
                tree.selection_remove(item)

        ttk.Button(sel_frame, text="全选", command=select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(sel_frame, text="取消全选", command=deselect_all).pack(side=tk.LEFT, padx=2)

        # 底部按钮
        btn_frame = ttk.Frame(top, padding="6")
        btn_frame.pack(fill=tk.X)

        ttk.Label(btn_frame, text="⚠ 需要管理员权限",
                  foreground="#c62828", font=("", 9)).pack(side=tk.LEFT)

        def do_cleanup():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择要清理的 COM 口", parent=top)
                return

            if not messagebox.askyesno("确认",
                    f"确定要删除 {len(selected)} 个幽灵 COM 口的注册表记录？\n"
                    "此操作需要管理员权限。", parent=top):
                return

            from usb_monitor.core import cleanup_ghost_com_port

            top.destroy()

            results = []
            for port_name in selected:
                # 找到对应的 ghost port 对象
                g = next((g for g in ghosts if g.port == port_name), None)
                if g:
                    ok = cleanup_ghost_com_port(g)
                    results.append((port_name, "✅ 已删除" if ok else "❌ 失败"))
                else:
                    results.append((port_name, "❌ 未找到"))

            # 显示结果
            result_msg = "\n".join(f"{p}: {s}" for p, s in results)
            success_count = sum(1 for _, s in results if "✅" in s)
            messagebox.showinfo(
                "清理结果",
                f"成功清理 {success_count}/{len(results)} 个幽灵 COM 口\n\n{result_msg}"
            )
            self.refresh()

        ttk.Button(btn_frame, text="删除选中",
                   command=do_cleanup).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="取消",
                   command=top.destroy).pack(side=tk.RIGHT, padx=2)

    def _find_group_for_device(self, dev: USBDevice) -> Optional[str]:
        """返回设备所属的分组 iid，或 None。"""
        key = dev.group_key
        gid = _group_iid(key)
        if self.tree.exists(gid):
            return gid
        return None

    # ── 详情面板 ──────────────────────────────────────────────────────────

    def _on_select(self, event):
        selected = self.tree.selection()
        if not selected:
            self.lbl_detail.config(text="选择一个设备查看详情", foreground="#888")
            return

        iid = selected[0]

        # 如果选中了分组头，显示分组摘要
        if iid.startswith(GROUP_PREFIX):
            key = iid[len(GROUP_PREFIX):]
            g = self._device_groups.get(key)
            if g:
                lines = [f"📦 物理设备组: {g.display_name}", ""]
                for d in g.all_devices():
                    lines.append(f"  • {d.name}  [{d.device_class}]")
                self.lbl_detail.config(
                    text="\n".join(lines),
                    foreground="#555",
                )
            return

        dev = self._devices.get(iid)
        if not dev:
            self.lbl_detail.config(text="<设备信息不可用>", foreground="#888")
            return

        detail = (
            f"名称:       {dev.name}\n"
            f"描述:       {dev.description or 'N/A'}\n"
            f"制造商:     {dev.manufacturer or 'N/A'}\n"
            f"状态:       {dev.status}\n"
            f"VID:PID:    {dev.vendor_id}:{dev.product_id}\n"
            f"序列号:     {dev.serial_number or 'N/A'}\n"
            f"设备类别:   {dev.device_class or 'N/A'}\n"
            f"驱动版本:   {dev.driver_version or 'N/A'}\n"
            f"服务名:     {dev.service or 'N/A'}\n"
            f"PNP ID:     {dev.pnp_device_id}"
        )
        self.lbl_detail.config(text=detail, foreground="#000")

    # ── 实时监控 ──────────────────────────────────────────────────────────

    def toggle_monitor(self):
        if self._monitor_active:
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self):
        self._monitor_active = True
        self.btn_monitor.config(text="⏹ 停止监控")
        self.refresh()
        self.btn_refresh.config(state=tk.DISABLED)
        self.lbl_count.config(text="监控中...")

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True,
            name="USB-Monitor"
        )
        self._monitor_thread.start()

    def _stop_monitor(self):
        self._monitor_active = False
        self.btn_monitor.config(text="▶ 实时监控")
        self.btn_refresh.config(state=tk.NORMAL)
        total = len(self.tree.get_children())
        self.lbl_count.config(text=f"共 {total} 个设备" if not self._grouping else "")

    def _monitor_loop(self):
        """后台线程：轮询检测设备插拔。"""
        from usb_monitor.core import enum_usb_devices, enum_usb_devices_fast

        poll = 2.0
        fallback = False

        # 子线程初始化 COM
        try:
            import pythoncom
            pythoncom.CoInitialize()
            _com_inited = True
        except ImportError:
            _com_inited = False

        def _scan():
            nonlocal fallback
            if not fallback:
                try:
                    return enum_usb_devices()
                except Exception:
                    fallback = True
                    self.root.after(0, lambda: self.lbl_count.config(
                        text="监控中 (wmic 模式)"
                    ))
            return enum_usb_devices_fast()

        # 首次扫描
        try:
            initial = _scan()
        except Exception:
            initial = []
        known = {d.pnp_device_id: d for d in initial}

        # 初始填充
        self.root.after(0, lambda: self._store_devices(list(known.values())))
        self.root.after(0, lambda: self._populate_tree(list(known.values())))

        while self._monitor_active:
            time.sleep(poll)
            try:
                current = _scan()
            except Exception:
                continue

            current_map = {d.pnp_device_id: d for d in current}

            # 插入
            for pid, dev in current_map.items():
                if pid not in known:
                    self.root.after(0, lambda d=dev: self._on_insert(d))

            # 拔出
            for pid, dev in known.items():
                if pid not in current_map:
                    self.root.after(0, lambda d=dev: self._on_remove(d))

            known = current_map

        if _com_inited:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    # ── 插拔事件处理 ──────────────────────────────────────────────────────

    def _on_insert(self, dev: USBDevice):
        """设备插入（UI 线程调用）。"""
        if dev.pnp_device_id in self._devices:
            return  # 已存在
        self._devices[dev.pnp_device_id] = dev

        # 重建整棵树（分组依赖关系可能变化）
        all_devices = list(self._devices.values())
        self._populate_tree(all_devices)
        self._store_devices(all_devices)

        # 高亮新设备
        status_tag = "OK" if dev.status == "OK" else "Error"
        try:
            self.tree.item(dev.pnp_device_id, tags=(status_tag, "insert_anim"))
            self.tree.see(dev.pnp_device_id)
            self.tree.after(2000, lambda: self.tree.item(
                dev.pnp_device_id, tags=(status_tag,)
            ))
        except Exception:
            pass

    def _on_remove(self, dev: USBDevice):
        """设备拔出（UI 线程调用）。"""
        self._devices.pop(dev.pnp_device_id, None)

        # 标记移除动画
        try:
            self.tree.item(dev.pnp_device_id, tags=("remove_anim",))
        except Exception:
            pass

        # 短暂延迟后重建树
        self.tree.after(800, self._rebuild_after_remove)

    def _rebuild_after_remove(self):
        """移除动画后重建树。"""
        all_devices = list(self._devices.values())
        self._populate_tree(all_devices)
        self._store_devices(all_devices)

    # ── 主循环 ───────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


def main():
    gui = USBGUI()
    gui.run()


if __name__ == "__main__":
    main()
