"""选择满足 requirements.txt 的 Python，只为当前文件夹准备缺少的依赖。"""
from importlib import import_module
from importlib.metadata import version
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
REQUIREMENTS = ROOT / "requirements.txt"
VENV = ROOT / ".venv"


def ready():
    if sys.version_info < (3, 11):
        return False
    try:
        try:
            from packaging.requirements import Requirement
        except ImportError:
            from pip._vendor.packaging.requirements import Requirement
        for line in REQUIREMENTS.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                dependency = Requirement(line)
                if version(dependency.name) not in dependency.specifier:
                    return False
                import_module(dependency.name)
        return True
    except (ImportError, OSError, ValueError):
        return False


def interpreter_ready(python):
    if not python.is_file():
        return False
    if python.resolve() == Path(sys.executable).resolve():
        return ready()
    try:
        return subprocess.run([str(python), "-B", "-X", "utf8", str(SCRIPT), "--check"],
                              cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def main():
    if sys.argv[1:] == ["--check"]:
        return int(not ready())
    installed = Path(sys.executable)
    local = VENV / "Scripts" / "python.exe"
    python = next((candidate for candidate in (local, installed) if interpreter_ready(candidate)), None)
    if not python:
        if not local.exists():
            import venv
            print("正在当前文件夹创建独立 Python 环境…", flush=True)
            venv.EnvBuilder(with_pip=True).create(VENV)
        print("正在安装或补齐依赖，首次使用可能需要几分钟…", flush=True)
        subprocess.run([str(local), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(REQUIREMENTS)], cwd=ROOT, check=True)
        if not interpreter_ready(local):
            print("依赖未能正常载入，请检查安装提示后重试。", flush=True)
            return 1
        python = local
    windowless = python.with_name("pythonw.exe")
    # 当前目录与数据处理位置始终从项目自身推导。服务日志不记录网页输入。
    env = dict(os.environ)
    env.pop("TUSHARE_TOKEN", None)
    subprocess.Popen([str(windowless if windowless.exists() else python), "-B", "-X", "utf8",
                      str(SCRIPT.with_name("dashboard.pyw"))], cwd=ROOT, env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print("未能启动。请确认已将项目完整解压至可写文件夹，网络能连接依赖下载源。", flush=True)
        raise SystemExit(1) from error
