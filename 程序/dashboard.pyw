"""接收本机网页提交的 Tushare 凭据，在后台分别更新三组数据。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import NamedTemporaryFile
import threading
import time
from urllib.parse import quote, unquote, urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "程序"
DATA = ROOT / "数据"
HTML = "打开图表.html"
DEFAULT_PORT = 18736
MANIFEST = PROGRAM / "本机服务.js"
EMPTY_MANIFEST = "window.PUBLIC_DASHBOARD_SERVICE=null;\n"
JOBS = (
    ("图①④⑤ 指数、ETF 份额和融资余额", "series_build.py", ("series",), "1990-12-19"),
    ("图②③ 宽基 ETF 申赎", "etf_build.py", ("flows", "marketflow"), "2005-01-01"),
    ("图⑥ 股息率与国债收益率", "yields_build.py", ("yields",), "2005-01-01"),
)
DATA_FILES = {"数据/" + name + ".js" for _, _, names, _ in JOBS for name in names}
ASSETS = {HTML, *DATA_FILES, *("程序/" + name for name in
          ("style.css", "ui.js", "engine.js", "live.js", "vendor/echarts.min.js",
           "public-entry.js", "public-entry.css"))}


def closed_day():
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.date() if now.hour >= 15 else now.date() - timedelta(days=1)


def version():
    stamps = []
    for name in sorted(ASSETS):
        try:
            entry = (ROOT / name).stat()
            stamps.append((name, entry.st_mtime_ns, entry.st_size))
        except FileNotFoundError:
            pass
    return sha256(str(stamps).encode()).hexdigest()[:16]


def has_data():
    return all((ROOT / name).is_file() for name in DATA_FILES)


def replace_text(path, text):
    with NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        out.write(text)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def update_lock():
    DATA.mkdir(exist_ok=True)
    with (DATA / ".更新锁").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def check_token(token):
    import requests

    # 图②③所需的 ETF 日线是建库前首先要验证的权限。网页仅接收固定说明。
    try:
        response = requests.post("https://api.tushare.pro",
            json={"api_name": "fund_daily", "token": token, "fields": "ts_code,trade_date,close",
                  "params": {"ts_code": "510300.SH", "trade_date": "20240102"}},
            timeout=(7, 20), allow_redirects=False)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return "无法连接 Tushare 或响应异常。请检查网络后重试。"
    if not isinstance(result, dict):
        return "Tushare 响应格式异常，请稍后重试。"
    code = result.get("code")
    if code != 0:
        reason = str(result.get("msg", "")).lower()
        if any(word in reason for word in ("频次", "频率", "每分钟", "次数", "频繁", "rate limit")):
            return "Tushare 查询频次已达上限，请稍后重试。"
        if code == 40203 or "权限" in reason or "积分" in reason:
            return "该账号缺少 ETF 日线 fund_daily 权限，图②、③需要该接口。官方目前要求至少 5000 积分，请核对权限后重试。"
        if code in (40001, 40101) or any(word in reason for word in ("token", "凭证", "令牌")):
            return "Tushare 未接受这个 Token。请从个人中心重新复制完整 Token 后重试。"
        return "Tushare 暂时无法完成 ETF 日线查询，请稍后重试。"
    if not (result.get("data") or {}).get("items"):
        return "Tushare 未返回验证所需的 ETF 行情。请稍后重试。"
    return ""


def job_error(output):
    if "图⑥部分更新，股息率未更新" in output:
        return "股息率未更新，国债曲线已检查"
    if "图⑥部分更新，国债未更新" in output:
        return "国债曲线未更新，股息率已检查"
    if "ModuleNotFoundError" in output:
        return "缺少依赖，请重新运行打开资金图表.cmd"
    if "权限" in output or "40203" in output:
        return "请核对接口权限和访问频次"
    if any(value in output.lower() for value in ("timeout", "timed out", "connectionerror", "proxyerror")):
        return "上游连接超时或失败"
    return "来源响应或构建异常，可稍后重试"


class Update:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.token = None
        self.last_target = None
        self.last_seen = time.monotonic()
        self.from_file = threading.Event()
        self.set_state(False, True, "输入自己的 Tushare Token，即可生成或更新六张图。")

    def set_state(self, busy, requires_token, message):
        with self.lock:
            self.state = {"busy": busy, "requiresToken": requires_token, "message": message,
                          "csrfToken": self.csrf, "hasData": has_data(), "version": version()}

    def submit(self, token):
        with self.lock:
            if self.state["busy"]:
                return 409, "正在更新，请等本次计算完成。"
            self.token = None
            self.last_target = self.args.end or closed_day()
            self.set_state(True, False, "正在向 Tushare 验证 Token…")
            threading.Thread(target=self.work, args=(token, self.last_target), daemon=True).start()
            return 202, "已开始验证，随后将自动计算。"

    def status(self):
        with self.lock:
            if self.token and not self.state["busy"] and self.last_target != (self.args.end or closed_day()):
                self.submit(self.token)
            return dict(self.state)

    def work(self, token, target):
        try:
            issue = check_token(token)
            if issue:
                self.set_state(False, True, issue)
                return
            self.set_state(True, False, "Token 已验证，正在准备本地数据目录。")
            with update_lock():
                issues = self.run_jobs(token, target)
            with self.lock:
                self.token = token if not issues else None
                self.set_state(False, bool(issues), "部分更新未完成，" + "、".join(issues) + "。可重新提交重试。"
                               if issues else "六张图已完成更新。各图标注了实际观测日期。")
        except Exception:
            self.set_state(False, True, "更新中断。请检查文件夹是否可写，以及依赖是否安装成功，然后重试。")

    def run_jobs(self, token, target):
        # 凭据只存在于本服务内存和本次更新子进程的环境中；不读用户的 SDK 凭据文件。
        env = dict(os.environ, PYTHONUTF8="1", TUSHARE_TOKEN=token)
        issues = []
        for label, script, names, first in JOBS:
            self.set_state(True, False, "正在更新" + label + "…")
            args = [sys.executable, "-B", "-X", "utf8", str(PROGRAM / script), "--end", target.isoformat()]
            outputs = [DATA / (name + ".js") for name in names]
            if self.args.start:
                args.extend(["--start", self.args.start.isoformat()])
            elif all(path.is_file() for path in outputs):
                args.append("--daily")
            else:
                args.extend(["--start", first])
            result = subprocess.run(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not all(path.is_file() for path in outputs):
                issues.append(label + "（" + job_error(result.stdout) + "）")
        return issues


# 从 file:// 打开的页面通过 MessagePort 交给已验证的本地实例。
# proof 从服务端返回；capability 必须从同一项目内的短期本地文件取得。
BRIDGE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>本机连接</title></head>
<body><script>
if (parent !== window) {
    const proof = __PROOF__;
    const channel = new MessageChannel();
    channel.port1.onmessage = async ({data}) => {
        if (!data || data.type !== 'funds-configure' || typeof data.token !== 'string') return;
        channel.port1.onmessage = null;
        let status = 0, message = '无法连接本机服务。';
        try {
            const response = await fetch('/bridge-configure', {
                method: 'POST', headers: {'Content-Type':'application/json',
                    'X-CSRF-Token':__CSRF__, 'X-File-Capability':data.capability},
                body: JSON.stringify({token:data.token}), cache:'no-store'
            });
            status = response.status;
            message = (await response.json()).message;
        } catch {}
        channel.port1.postMessage({type:'funds-configured', proof, ok:status===202, status, message});
        channel.port1.close();
    };
    parent.postMessage({type:'funds-bridge-ready', proof}, '*', [channel.port2]);
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data, content_type, *, bridge=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not bridge:
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json_reply(self, status, payload):
        self.reply(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.headers.get("Host") != self.server.local_host:
            self.json_reply(403, {"message": "仅限本机地址。"})
            return
        route = unquote(urlsplit(self.path).path).lstrip("/") or HTML
        self.server.update.last_seen = time.monotonic()
        if route == "status.json":
            self.json_reply(200, self.server.update.status())
        elif route == "instance":
            self.json_reply(200, {"proof": self.server.proof})
        elif route == "bridge":
            page = BRIDGE_PAGE.replace("__PROOF__", json.dumps(self.server.proof)).replace(
                "__CSRF__", json.dumps(self.server.update.csrf))
            self.reply(200, page.encode(), "text/html; charset=utf-8", bridge=True)
        elif route in ASSETS and (ROOT / route).is_file():
            mime = mimetypes.guess_type(route)[0] or "application/octet-stream"
            self.reply(200, (ROOT / route).read_bytes(), mime)
        elif route in DATA_FILES:
            self.reply(200, b"/* Local data pending. */", "text/javascript; charset=utf-8")
        else:
            self.json_reply(404, {"message": "文件不存在。"})

    def do_POST(self):
        route = urlsplit(self.path).path
        if route not in ("/configure", "/bridge-configure"):
            self.json_reply(404, {"message": "入口不存在。"})
            return
        update = self.server.update
        same_origin = (self.headers.get("Host") == self.server.local_host and
                       self.headers.get("Origin") == self.server.origin and
                       self.headers.get("X-CSRF-Token") == update.csrf)
        if not same_origin or (route == "/bridge-configure" and
                               self.headers.get("X-File-Capability") != self.server.capability):
            self.json_reply(403, {"message": "页面与本地服务不匹配，请重新打开看板。"})
            return
        if self.headers.get_content_type() != "application/json":
            self.json_reply(415, {"message": "请求格式不正确。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                raise ValueError
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            token = payload["token"].strip()
            if not 0 < len(token) <= 512 or not token.isascii() or not token.isprintable() or " " in token:
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            self.json_reply(400, {"message": "请输入完整的 Tushare Token。"})
            return
        update.last_seen = time.monotonic()
        status, message = update.submit(token)
        if route == "/bridge-configure" and status == 202:
            update.from_file.set()
        self.json_reply(status, {"message": message})


def existing_service():
    try:
        value = MANIFEST.read_text(encoding="utf-8").strip()
        info = json.loads(value.removeprefix("window.PUBLIC_DASHBOARD_SERVICE=").removesuffix(";"))
        if not info or type(info["port"]) is not int or not 0 < info["port"] < 65536:
            return None
        connection = HTTPConnection("127.0.0.1", info["port"], timeout=1)
        try:
            connection.request("GET", "/instance")
            reply = connection.getresponse()
            if reply.status == 200 and json.loads(reply.read(1024)).get("proof") == info["proof"]:
                return "http://127.0.0.1:" + str(info["port"]) + "/" + quote(HTML) + "?v=" + version()
        finally:
            connection.close()
    except (OSError, ValueError, KeyError, TypeError, HTTPException):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="用浏览器输入 Token 并生成市场数据六图")
    parser.add_argument("--start", type=date.fromisoformat, help="指定首次研究的起始日期")
    parser.add_argument("--end", type=date.fromisoformat, help="指定研究截止日期")
    parser.add_argument("--no-browser", action="store_true", help="打印本机入口地址")
    parser.add_argument("--port", type=int, help="固定测试端口，0 表示由系统选择")
    args = parser.parse_args()
    if (args.start and args.start > (args.end or closed_day())) or (args.end and args.end > closed_day()):
        parser.error("日期范围应止于最近已收盘日期")
    if not any((args.no_browser, args.start, args.end, args.port is not None)):
        previous = existing_service()
        if previous:
            webbrowser.open(previous)
            return
    update = Update(args)
    if args.port is None:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), Handler)
        except OSError:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    else:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    with server:
        server.update = update
        server.timeout = 1
        server.local_host = "127.0.0.1:" + str(server.server_port)
        server.origin = "http://" + server.local_host
        server.proof = secrets.token_urlsafe(32)
        server.capability = secrets.token_urlsafe(32)
        info = dict(port=server.server_port, proof=server.proof, capability=server.capability)
        active_manifest = "window.PUBLIC_DASHBOARD_SERVICE=" + json.dumps(info, separators=(",", ":")) + ";\n"
        replace_text(MANIFEST, active_manifest)
        url = server.origin + "/" + quote(HTML) + "?v=" + version()
        if args.no_browser:
            print(url, flush=True)
        else:
            def open_if_needed():
                if not update.from_file.wait(4):
                    webbrowser.open(url)
            threading.Thread(target=open_if_needed, daemon=True).start()
        try:
            while update.state["busy"] or time.monotonic() - update.last_seen < 300:
                server.handle_request()
        finally:
            update.token = None
            if MANIFEST.is_file() and MANIFEST.read_text(encoding="utf-8") == active_manifest:
                replace_text(MANIFEST, EMPTY_MANIFEST)


if __name__ == "__main__":
    main()
