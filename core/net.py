"""下载器：支持进度回调、断点续传、重试、可选 SHA-256 校验。"""
import hashlib
import os
import time
import urllib.error
import urllib.request


class DownloadError(Exception):
    pass


def _open_with_retry(url: str, dest: str, timeout: int = 60, retries: int = 3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OneClickEnv/0.1",
                "Accept-Encoding": "identity",
            })
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                req.add_header("Range", "bytes=%d-" % os.path.getsize(dest))
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp, os.path.getsize(dest) if os.path.exists(dest) else 0
        except urllib.error.HTTPError as e:
            if e.code == 416:  # 已下载完整
                return None, os.path.getsize(dest)
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(1.5 * attempt)
    raise DownloadError("下载失败（重试 %d 次后）：%s" % (retries, last_err))


def download(url: str, dest: str, progress_cb=None, sha256: str = "",
             cancel_evt=None) -> str:
    """下载 url 到 dest；dest 存在则断点续传；可选校验 SHA-256。返回最终文件路径。

    cancel_evt：threading.Event，置位时中断下载并保留 .part 文件（下次可断点续传）。
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    temp = dest + ".part"
    resp, offset = _open_with_retry(url, temp)
    if resp is None:  # 416：已经完整
        if os.path.exists(dest):
            return dest
        os.replace(temp, dest)
        return dest

    total = (resp.headers.get("Content-Length") or 0)
    try:
        total = int(total) + offset if total else 0
    except (TypeError, ValueError):
        total = 0
    mode = "ab" if offset else "wb"
    done = offset
    last_t = time.time()
    try:
        with open(temp, mode) as f:
            while True:
                if cancel_evt and cancel_evt.is_set():
                    raise DownloadError("已取消下载（保留已下载部分，可续传）")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb and (time.time() - last_t >= 0.1 or done >= (total or done)):
                    progress_cb(done, total)
                    last_t = time.time()
    finally:
        resp.close()

    if total and done < total:
        raise DownloadError("下载中断：已获取 %d/%d 字节" % (done, total))

    if sha256:
        h = hashlib.sha256()
        with open(temp, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest().lower() != sha256.lower():
            os.remove(temp)
            raise DownloadError("SHA-256 校验失败")
    os.replace(temp, dest)
    return dest
