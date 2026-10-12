"""本地状态库（JSON）：已安装记录、PATH 快照索引、托管安装目录。

数据目录可配置：注册表 HKCU\\Software\\OneClickEnv\\data_root 记录位置，
默认 %LOCALAPPDATA%\\OneClickEnv；托管安装 / 下载缓存 / 状态与快照均在数据目录下，
修改目录时整体迁移（见 migrate_data_root）。
"""
import json
import os
import shutil
import time
import winreg

_REG_KEY = r"Software\OneClickEnv"


def _default_root() -> str:
    appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    return os.path.join(appdata, "OneClickEnv")


def get_data_root() -> str:
    """读取当前数据目录（注册表优先，无则默认）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            v, _ = winreg.QueryValueEx(k, "data_root")
            if v:
                return os.path.abspath(os.path.normpath(v))
    except OSError:
        pass
    return _default_root()


def set_data_root(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
        winreg.SetValueEx(k, "data_root", 0, winreg.REG_SZ, os.path.abspath(path))


def app_root() -> str:
    root = get_data_root()
    os.makedirs(root, exist_ok=True)
    return root


def data_root() -> str:
    return app_root()


def migrate_data_root(new_root: str) -> str:
    """把整个数据目录迁移到新位置（managed/downloads/snapshots/state.json 一起搬）。

    返回状态：ok / same / invalid / nested / target_not_empty / move_failed:原因。
    """
    new_root = os.path.abspath(os.path.normpath(new_root))
    if not new_root or len(new_root) < 3:
        return "invalid"
    old = os.path.abspath(os.path.normpath(get_data_root()))
    if os.path.normcase(old) == os.path.normcase(new_root):
        return "same"
    # 互为父子目录会导致 move 自身，拒绝
    if (os.path.normcase(new_root).startswith(os.path.normcase(old) + os.sep) or
            os.path.normcase(old).startswith(os.path.normcase(new_root) + os.sep)):
        return "nested"
    # 目标已存在且有内容 → 拒绝覆盖
    if os.path.exists(new_root) and any(os.scandir(new_root)):
        return "target_not_empty"
    parent = os.path.dirname(new_root)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(old):
        for sub in ("managed", "downloads", "snapshots"):
            os.makedirs(os.path.join(old, sub), exist_ok=True)
        try:
            shutil.move(old, new_root)
        except Exception as e:  # noqa: BLE001
            return "move_failed:" + str(e)
    else:
        os.makedirs(new_root, exist_ok=True)
    set_data_root(new_root)
    return "ok"


def managed_root() -> str:
    root = os.path.join(app_root(), "managed")
    os.makedirs(root, exist_ok=True)
    return root


def downloads_dir() -> str:
    root = os.path.join(app_root(), "downloads")
    os.makedirs(root, exist_ok=True)
    return root


def state_file() -> str:
    return os.path.join(app_root(), "state.json")


def _load() -> dict:
    if os.path.exists(state_file()):
        try:
            with open(state_file(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"installed": {}, "snapshots": {}, "settings": {}}


def _save(state: dict) -> None:
    tmp = state_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_file())


def get_settings() -> dict:
    return _load().get("settings", {})


def save_settings(patch: dict) -> dict:
    state = _load()
    state.setdefault("settings", {}).update(patch)
    _save(state)
    return state["settings"]


def installed_records() -> dict:
    return _load().get("installed", {})


def record_install(env_id: str, record: dict) -> None:
    state = _load()
    state.setdefault("installed", {})[env_id] = record
    _save(state)


def record_uninstall(env_id: str) -> None:
    state = _load()
    state.get("installed", {}).pop(env_id, None)
    _save(state)


def get_record(env_id: str) -> dict:
    return installed_records().get(env_id, {})


def record_snapshot(snap: dict) -> None:
    state = _load()
    state.setdefault("snapshots", {})[snap["id"]] = snap
    _save(state)


def snapshot_index() -> dict:
    return _load().get("snapshots", {})
