"""安装 / 卸载 / 版本切换引擎。

通道：
- winget：主流软件静默安装（--silent --accept-package-agreements）
- zip：托管式多版本（JDK / Node / Nginx / Redis / Tomcat / Maven / FFmpeg），写入托管目录并管理 PATH
- exe / msi：下载官方安装包后静默安装
- guide：打开官方下载页（不自动安装，避免配置不可控）

安全：所有 PATH / 环境变量修改前先快照；卸载只删除本工具安装的托管目录与本工具添加的 PATH 条目。
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
import zipfile

from core import detect, net, pathman, state


def log(cb, msg):
    if cb:
        cb(msg)


def _run(proc_cmd: list, log_cb, timeout: int = 600, cancel_evt=None) -> int:
    log(log_cb, "> " + " ".join(proc_cmd))
    proc = subprocess.Popen(
        proc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    start = time.time()
    while True:
        if cancel_evt and cancel_evt.is_set():
            try:
                proc.terminate()
            except Exception:
                pass
            raise RuntimeError("已取消安装（进程已终止）")
        rc = proc.poll()
        if rc is not None:
            break
        if time.time() - start > timeout:
            try:
                proc.terminate()
            except Exception:
                pass
            raise RuntimeError("安装超时（已终止）")
        time.sleep(0.2)
    out, err = proc.communicate()
    tail = ((out or "") + (err or "")).strip()
    if tail:
        for line in tail.splitlines()[-8:]:
            log(log_cb, "  " + line)
    return rc


# ---------------- 下载与解压 ----------------

def _download(url: str, log_cb, progress_cb=None, cancel_evt=None) -> str:
    dest = os.path.join(state.downloads_dir(), os.path.basename(url).split("?")[0] or "download.bin")
    log(log_cb, "下载中：%s" % url)
    return net.download(url, dest, progress_cb=progress_cb, cancel_evt=cancel_evt)


def _extract_zip(zip_path: str, target_dir: str, log_cb) -> str:
    """解压到 target_dir，返回顶层目录（去除唯一顶层目录）。"""
    os.makedirs(target_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        top_level = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(top_level) == 1:
            base = os.path.join(target_dir, top_level.pop())
            if os.path.exists(base):
                shutil.rmtree(base, ignore_errors=True)
        log(log_cb, "解压中：%s" % os.path.basename(zip_path))
        zf.extractall(target_dir)
    # 返回唯一顶层目录
    names = zipfile.ZipFile(zip_path).namelist()
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        return os.path.join(target_dir, tops.pop())
    return target_dir


def _resolve_node_url(major: str) -> str:
    """通过 nodejs.org/dist/index.json 取某主版本线的最新版。"""
    req = urllib.request.Request("https://nodejs.org/dist/index.json",
                                 headers={"User-Agent": "OneClickEnv/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candidates = [d["version"] for d in data if d["version"].startswith("v" + major + ".")]
    if not candidates:
        raise RuntimeError("Node.js 主版本 %s 无可用版本" % major)
    ver = candidates[0]
    return "https://nodejs.org/dist/%s/node-%s-win-x64.zip" % (ver, ver)


def resolve_url(entry: dict, version: str) -> str:
    if entry.get("node_major"):
        return _resolve_node_url(version)
    return entry["url_template"].replace("{version}", version)


def install_managed(entry: dict, version: str, log_cb, progress_cb=None,
                    cancel_evt=None) -> dict:
    """托管式安装，返回 record。"""
    env_id = entry["id"]
    url = resolve_url(entry, version)
    dest = _download(url, log_cb, progress_cb, cancel_evt)
    ver_dir = os.path.join(state.managed_root(), env_id, version)
    top = _extract_zip(dest, ver_dir, log_cb)
    install_dir = top if os.path.isdir(top) else ver_dir

    snapshot = pathman.create_snapshot("安装 %s v%s" % (entry["name"], version))
    added_path = []
    try:
        env = entry.get("install", {}).get("env", {}) or {}
        rel = env.get("PATH_APPEND", "")
        if rel:
            target = os.path.normpath(os.path.join(install_dir, rel)) if rel != "." else install_dir
            if pathman.append_path_entry(target):
                added_path.append(target)
        env_vars = entry.get("install", {}).get("env_var_set", {}) or {}
        for name, tmpl in env_vars.items():
            pathman.set_user_env(name, tmpl.replace("{dir}", install_dir))
    except Exception:
        pathman.restore_snapshot(snapshot["id"])
        raise
    record = {
        "env_id": env_id, "version": version, "source": "managed",
        "install_dir": install_dir, "added_path": added_path,
        "env_vars": dict(entry.get("install", {}).get("env_var_set", {}) or {}),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_id": snapshot["id"],
    }
    state.record_install(env_id, record)
    return record


def install_winget(entry: dict, log_cb, cancel_evt=None) -> dict:
    ids = entry["install"].get("winget_ids") or [entry["install"]["winget_id"]]
    for wid in ids:
        rc = _run(["winget", "install", "--id", wid, "-e", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements"],
                  log_cb, cancel_evt=cancel_evt)
        if rc != 0:
            raise RuntimeError("winget 安装失败（%s），exit=%d" % (wid, rc))
    record = {
        "env_id": entry["id"], "version": "", "source": "winget",
        "winget_ids": ids, "install_dir": "", "added_path": [],
        "env_vars": {}, "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.record_install(entry["id"], record)
    return record


def install_exe(entry: dict, log_cb, progress_cb=None, cancel_evt=None) -> dict:
    inst = entry["install"]
    file_path = _download(inst["url"], log_cb, progress_cb, cancel_evt)
    cmd = [file_path] + (inst.get("silent_args", "").split())
    rc = _run(cmd, log_cb, timeout=900, cancel_evt=cancel_evt)
    if rc not in (0, 3010):
        raise RuntimeError("安装程序退出码异常：%d（3010 表示需重启）" % rc)
    record = {
        "env_id": entry["id"], "version": "", "source": "exe",
        "install_dir": "", "added_path": [], "env_vars": {},
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.record_install(entry["id"], record)
    return record


def install_msi(entry: dict, log_cb, progress_cb=None, cancel_evt=None) -> dict:
    inst = entry["install"]
    file_path = _download(inst["url"], log_cb, progress_cb, cancel_evt)
    args = (inst.get("silent_args", "/qn /norestart")).split()
    cmd = ["msiexec", "/i", file_path] + args
    rc = _run(cmd, log_cb, timeout=900, cancel_evt=cancel_evt)
    if rc not in (0, 3010):
        raise RuntimeError("MSI 安装退出码异常：%d" % rc)
    record = {
        "env_id": entry["id"], "version": "", "source": "msi",
        "install_dir": "", "added_path": [], "env_vars": {},
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.record_install(entry["id"], record)
    return record


def install_env(entry: dict, version: str = "", log_cb=None, progress_cb=None) -> dict:
    inst = entry.get("install", {}) or {}
    itype = inst.get("type", "guide")
    if itype == "zip":
        return install_managed(entry, version or (inst.get("versions") or [""])[0], log_cb, progress_cb)
    if itype == "winget":
        return install_winget(entry, log_cb)
    if itype == "exe":
        return install_exe(entry, log_cb, progress_cb)
    if itype == "msi":
        return install_msi(entry, log_cb, progress_cb)
    raise RuntimeError("该环境为引导型，请在界面中打开官方下载页手动安装")


def verify(entry: dict, record: dict = None) -> tuple:
    """冒烟验证，返回 (ok, message)。"""
    smoke = entry.get("install", {}).get("smoke") or []
    if not smoke:
        return True, "（无冒烟命令，按检测结果判断）"
    if record and record.get("source") == "managed" and record.get("install_dir"):
        cmd = smoke[:]
        exe = cmd[0]
        candidate = os.path.join(record["install_dir"], "bin", exe)
        if not os.path.exists(candidate):
            candidate = os.path.join(record["install_dir"], exe)
        if os.path.exists(candidate):
            cmd = [candidate] + cmd[1:]
            ok, out = detect.run_cmd(cmd)
            if ok:
                return True, out.strip().splitlines()[0][:80]
    ok, out = detect.run_cmd(smoke)
    return (True, out.strip().splitlines()[0][:80]) if ok else (False, "验证失败：命令不可用")


# ---------------- 卸载 ----------------

def uninstall_env(entry: dict, log_cb) -> None:
    rec = state.get_record(entry["id"])
    source = rec.get("source") if rec else entry.get("uninstall", {}).get("type", "guide")
    log(log_cb, "卸载 %s（来源：%s）" % (entry["name"], source))
    snapshot = pathman.create_snapshot("卸载 %s" % entry["name"])

    if source == "managed":
        root = os.path.join(state.managed_root(), entry["id"])
        for p in rec.get("added_path", []):
            pathman.remove_path_entry(p)
        for name, value in (rec.get("env_vars") or {}).items():
            current = pathman.get_user_env(name)
            if current and current.rstrip("\\").lower() == value.replace("{dir}", rec["install_dir"]).rstrip("\\").lower():
                pathman.delete_user_env(name)
        shutil.rmtree(root, ignore_errors=True)
        state.record_uninstall(entry["id"])
        log(log_cb, "托管目录与 PATH 条目已清理")
        return

    if source == "winget":
        ids = rec.get("winget_ids") or [entry.get("install", {}).get("winget_id", "")]
        for wid in ids:
            rc = _run(["winget", "uninstall", "--id", wid, "-e", "--silent"], log_cb)
            if rc not in (0, -1):
                log(log_cb, "winget 卸载返回 %d（可能需手动确认）" % rc)
        state.record_uninstall(entry["id"])
        return

    if source == "registry":
        import winreg as wr
        pattern = entry.get("uninstall", {}).get("pattern", entry["name"])
        found = detect.registry_versions(pattern)
        if not found:
            raise RuntimeError("未在注册表找到可卸载条目：%s" % pattern)
        # 取首个 UninstallString 对应项
        for hkey, base in _uninstall_roots():
            try:
                with wr.OpenKey(hkey, base) as key:
                    for i in range(wr.QueryInfoKey(key)[0]):
                        try:
                            sub = wr.EnumKey(key, i)
                            with wr.OpenKey(key, sub) as sk:
                                try:
                                    name = wr.QueryValueEx(sk, "DisplayName")[0] or ""
                                except FileNotFoundError:
                                    continue
                                if pattern.lower() in name.lower():
                                    try:
                                        us = wr.QueryValueEx(sk, "UninstallString")[0]
                                    except FileNotFoundError:
                                        us = ""
                                    _run_uninstall_string(us, log_cb)
                                    state.record_uninstall(entry["id"])
                                    return
                        except OSError:
                            continue
            except OSError:
                continue
        raise RuntimeError("未找到可执行卸载命令")
    if source == "guide":
        raise RuntimeError("该环境为引导型，请通过官方卸载程序或“设置 > 应用”卸载")


def _uninstall_roots():
    import winreg as wr
    return [
        (wr.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (wr.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (wr.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]


def _run_uninstall_string(us: str, log_cb) -> None:
    if not us:
        return
    us = us.strip()
    if us.lower().startswith("msiexec") or "MsiExec" in us:
        code_match = None
        import re
        m = re.search(r"\{[0-9A-Fa-f-]{36}\}", us)
        if m:
            code_match = m.group(0)
        if code_match:
            _run(["msiexec", "/x", code_match, "/qn", "/norestart"], log_cb)
            return
    parts = us.split('"')
    cmd = [p for p in parts if p.strip()]
    _run(cmd, log_cb)


# ---------------- 版本切换 ----------------

def switch_version(entry: dict, version: str, log_cb) -> str:
    """切换托管环境默认版本（仅 zip 托管、switchable=true）。"""
    rec = state.get_record(entry["id"])
    root = os.path.join(state.managed_root(), entry["id"])
    ver_dir = os.path.join(root, version)
    if not os.path.isdir(ver_dir):
        raise RuntimeError("版本 v%s 未安装" % version)

    snapshot = pathman.create_snapshot("切换 %s -> v%s" % (entry["name"], version))
    # 移除该环境现有托管 PATH 条目
    if rec:
        for p in rec.get("added_path", []):
            pathman.remove_path_entry(p)
    # 找到版本目录（可能含唯一顶层子目录）
    tops = [d for d in os.listdir(ver_dir) if os.path.isdir(os.path.join(ver_dir, d))]
    base = os.path.join(ver_dir, tops[0]) if len(tops) == 1 else ver_dir
    env = entry.get("install", {}).get("env", {}) or {}
    rel = env.get("PATH_APPEND", "")
    target = os.path.normpath(os.path.join(base, rel)) if rel and rel != "." else base
    added = []
    if pathman.append_path_entry(target):
        added.append(target)
    for name, tmpl in (entry.get("install", {}).get("env_var_set", {}) or {}).items():
        pathman.set_user_env(name, tmpl.replace("{dir}", base))
    rec2 = dict(rec or {})
    rec2.update({"version": version, "added_path": added, "install_dir": base,
                 "env_vars": dict(entry.get("install", {}).get("env_var_set", {}) or {})})
    state.record_install(entry["id"], rec2)
    return base
