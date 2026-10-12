"""用户环境变量（PATH）管理：读取 / 写入 / 快照 / 回滚 / 单项增删。

安全约定：
- 只操作 HKCU\\Environment（用户级 PATH），不碰系统级；
- 每次修改前自动快照；失败可一键回滚；
- 只删除由本工具添加且与记录完全一致的 PATH 条目，绝不误删其他条目。
"""
import os
import re
import shutil
import time
import winreg

from core import state


def get_user_path() -> list:
    """读取用户 PATH，返回条目列表（含用户级 %USERPROFILE% 展开前原文）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        return []
    parts = value.split(";")
    return [p for p in parts if p.strip() != ""]


def set_user_path(entries: list) -> None:
    """整体写回用户 PATH（自动去重、去除空项）。"""
    seen = set()
    cleaned = []
    for p in entries:
        p = p.strip().rstrip("\\")
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(p)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(cleaned))
    # 广播环境变量变更（让新开终端立即生效）
    _broadcast()


def _broadcast() -> None:
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, None)
    except Exception:
        pass


def get_user_env(name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def set_user_env(name: str, value: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    _broadcast()


def delete_user_env(name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    _broadcast()


def snapshot_root() -> str:
    root = os.path.join(state.app_root(), "snapshots")
    os.makedirs(root, exist_ok=True)
    return root


def create_snapshot(reason: str) -> dict:
    """创建 PATH 快照，返回 {id, time, reason, path}。"""
    snap_id = time.strftime("%Y%m%d_%H%M%S") + "_" + str(int(time.time() * 1000) % 1000)
    path = os.path.join(snapshot_root(), snap_id + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(get_user_path()))
    return {"id": snap_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason, "path": path}


def list_snapshots() -> list:
    items = []
    for fn in sorted(os.listdir(snapshot_root()), reverse=True):
        if fn.endswith(".txt"):
            path = os.path.join(snapshot_root(), fn)
            mtime = time.localtime(os.path.getmtime(path))
            items.append({
                "id": fn[:-4], "time": time.strftime("%Y-%m-%d %H:%M:%S", mtime),
                "reason": _read_first_line(path), "path": path,
            })
    return items


def _read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            if first and "PATH 快照" not in first:
                return first
            return "(未记录原因)"
    except Exception:
        return "(未知)"


def restore_snapshot(snap_id: str) -> bool:
    path = os.path.join(snapshot_root(), snap_id + ".txt")
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        entries = [line.strip() for line in f.read().splitlines() if line.strip()]
    set_user_path(entries)
    return True


def remove_path_entry(exact: str) -> bool:
    """删除与 exact 完全一致的 PATH 条目（大小写不敏感）。"""
    entries = get_user_path()
    target = exact.rstrip("\\").lower()
    kept = [p for p in entries if p.rstrip("\\").lower() != target]
    if len(kept) != len(entries):
        set_user_path(kept)
        return True
    return False


def append_path_entry(entry: str) -> bool:
    entries = get_user_path()
    target = entry.rstrip("\\").lower()
    if any(p.rstrip("\\").lower() == target for p in entries):
        return False
    entries.append(entry)
    set_user_path(entries)
    return True
