"""环境检测：命令探测 / 注册表 / 目录检查 / 托管安装目录。"""
import os
import re
import subprocess
import winreg

from core import pathman, state


def run_cmd(cmd: list, timeout: int = 10) -> tuple:
    """返回 (ok, output_text)。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out
    except Exception:
        return False, ""


def registry_versions(pattern: str) -> list:
    """在卸载注册表中按 DisplayName 模糊匹配，返回 (version, location) 列表。"""
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    found = []
    for hkey, base in roots:
        try:
            with winreg.OpenKey(hkey, base) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub) as sk:
                            try:
                                name = winreg.QueryValueEx(sk, "DisplayName")[0]
                            except FileNotFoundError:
                                name = ""
                            if pat.search(name or ""):
                                try:
                                    ver = winreg.QueryValueEx(sk, "DisplayVersion")[0]
                                except FileNotFoundError:
                                    ver = ""
                                try:
                                    loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                                except FileNotFoundError:
                                    loc = ""
                                found.append((ver or "已安装", loc or ""))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def expand_vars(p: str) -> str:
    return os.path.expandvars(p.replace("%LOCALAPPDATA%", os.environ.get("LOCALAPPDATA", "")))


def detect_one(entry: dict) -> dict:
    """返回 {installed, version, detail, managed_versions}。"""
    detect = entry.get("detect", {}) or {}
    mode = detect.get("mode", "cmd")
    managed_versions = managed_installed_versions(entry)
    result = {"installed": False, "version": "", "detail": "", "managed_versions": managed_versions}

    if mode == "cmd":
        cmd = detect.get("cmd", [])
        ok, out = run_cmd(cmd)
        if ok and out.strip():
            regex = detect.get("regex", "")
            m = re.search(regex, out) if regex else None
            ver = m.group(1) if m else out.strip().splitlines()[0].strip()
            result.update({"installed": True, "version": ver, "detail": " ".join(cmd) + " 可用"})
        elif managed_versions:
            result["installed"] = True
            result["version"] = "托管 v" + ", ".join(managed_versions)
            result["detail"] = "已安装托管版本（系统命令未检测到 PATH 中的可执行文件）"
    elif mode == "registry":
        found = registry_versions(detect.get("pattern", ""))
        if found:
            versions = ", ".join(sorted({v for v, _ in found}))
            result.update({"installed": True, "version": versions, "detail": "注册表检测到 %d 项" % len(found)})
        elif managed_versions:
            result.update({"installed": True, "version": "托管 v" + ", ".join(managed_versions),
                           "detail": "已安装托管版本"})
    elif mode == "dir_check":
        path = expand_vars(detect.get("path", ""))
        if os.path.exists(path):
            result.update({"installed": True, "version": "已安装", "detail": "检测到 " + path})
    elif mode == "managed":
        if managed_versions:
            result.update({"installed": True, "version": "托管 v" + ", ".join(managed_versions),
                           "detail": "托管安装"})
    return result


def managed_installed_versions(entry: dict) -> list:
    """列出托管安装目录下已安装的版本。"""
    install = entry.get("install", {}) or {}
    if install.get("type") != "zip" or not install.get("managed"):
        return []
    root = os.path.join(state.managed_root(), entry["id"])
    if not os.path.exists(root):
        return []
    return sorted(os.listdir(root))


def detect_all(catalog: list, progress_cb=None) -> dict:
    results = {}
    for i, entry in enumerate(catalog):
        try:
            results[entry["id"]] = detect_one(entry)
        except Exception as e:  # noqa: BLE001
            results[entry["id"]] = {"installed": False, "version": "", "detail": "检测异常: %s" % e,
                                    "managed_versions": []}
        if progress_cb:
            progress_cb(i + 1, len(catalog))
    return results
