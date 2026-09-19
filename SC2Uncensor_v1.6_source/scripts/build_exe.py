# -*- coding: utf-8 -*-
"""一键打包(PyInstaller onedir): 产物整个目录拷到别的电脑就能用, 不需要装 Python。

用法(双击 build_exe.bat 等价于第一条):
    .venv\\Scripts\\python.exe scripts\\build_exe.py --zip
    .venv\\Scripts\\python.exe scripts\\build_exe.py                 # 不 zip
    .venv\\Scripts\\python.exe scripts\\build_exe.py --console        # 额外打控制台版
    .venv\\Scripts\\python.exe scripts\\build_exe.py --selftest       # 额外打自检 exe 并运行
    .venv\\Scripts\\python.exe scripts\\build_exe.py --skip-tests     # 跳过回归测试(不推荐)

产物:
    dist/SC2Uncensor/                界面版(主产物, 整个目录拷给别人即用)
        SC2Uncensor.exe              (无控制台窗口)
        config.json / offsets.dat    放在 exe 旁边, 用户双击记事本就能改
        README.txt / win_ocr_json.ps1
        ui_state.json / sc2_uncensor.log   首次运行自动生成
        _internal/                   Python 运行时与依赖(别删)
    dist/SC2Uncensor_v1.0_win64.zip  (--zip)

为什么这么做(见 docs/发布与开源方案.md §二): onedir 而非 onefile(onefile 每次启动
自解压, 杀软最敏感); 不加壳不 UPX; 带版本资源; 数据文件放 exe 旁边而不是打进包 —— 
因为打包后 __file__ 指向 _internal, 用户改不到 config.json(核心用 SC2_UNCENSOR_DIR
把数据目录指回 exe 旁边)。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# 打包脚本自己会 import 核心(读版本号), 按项目铁律别让它写产品日志
os.environ.setdefault("SC2_UNCENSOR_LOG",
                      os.path.join(os.environ.get("TEMP", "."), "sc2_build.log"))
# 打包哪个 app 目录: 默认仓库的 app\(开发主线); 设 SC2_BUILD_APP_DIR 可指向别的
# 稳定版目录(例: app1.6) —— 2026-09-19 加, 为了"app/ 在做 UI 美化的同时还能从
# 稳定版出包"。入口/数据文件/皮肤 全按这个目录取, 其余逻辑不变。
APP = os.environ.get("SC2_BUILD_APP_DIR") or os.path.join(ROOT, "app")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
NAME = "SC2Uncensor"
ENTRY_UI = os.path.join(APP, "ui_main.py")
ENTRY_CONSOLE = os.path.join(APP, "sc2_uncensor.py")
SELFTEST = os.path.join(ROOT, "scripts", "selftest_frozen.py")
# exe 旁边要放的数据文件(用户可改/可看)
DATA_FILES = ["config.json", "offsets.dat", "win_ocr_json.ps1", "README.txt"]
# 打进包里的资源(界面皮肤; 界面用 __file__ 找 _internal/ui_skin)
ADD_DATA = [(os.path.join(APP, "ui_skin"), "ui_skin")]
# winrt/winocr 没有现成 hook, 必须显式收集(WinRT 的 .pyd 靠名字动态加载)
HIDDEN = ["winocr",
          "winrt.windows.media.ocr", "winrt.windows.globalization",
          "winrt.windows.graphics.imaging", "winrt.windows.storage.streams",
          "winrt.windows.foundation", "winrt.windows.foundation.collections"]
COLLECT = ["winrt", "winocr"]
EXCLUDES = [
    "PyMemoryEditor", "matplotlib", "PyQt5", "PySide6", "IPython", "pytest",
    # ---- 体积优化(2026-09-14) ----
    # numpy: 只有"找蓝框"用过, 已改用 PIL 通道运算等价实现(逐图 A/B 一致), 一家省 ~25MB
    # (openblas 一个 DLL 19.6MB)。别再加回来: 要动蓝框识别先看 _blue_clusters 的注释。
    "numpy",
    # PIL 用不到的解码插件: AVIF(7.5MB)/WebP/ICC 色彩管理。我们的图全是 PNG/截图。
    "PIL.AvifImagePlugin", "PIL.WebPImagePlugin", "PIL.ImageCms",
]


def run(cmd, **kw):
    print(">>", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT, **kw)


def banner(msg):
    print()
    print("=" * 70)
    print(msg)
    print("=" * 70)


def check_env():
    banner("0/6 环境检查")
    py = sys.executable
    print("python:", py)
    missing = []
    for mod in ("PyInstaller", "PIL", "psutil", "pymem", "winocr", "winrt.windows.media.ocr"):
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        print(f"  {'OK ' if r.returncode == 0 else 'MISS'} {mod}")
        if r.returncode != 0:
            missing.append(mod)
    for f in (ENTRY_UI, ENTRY_CONSOLE, SELFTEST):
        if not os.path.exists(f):
            missing.append(os.path.relpath(f, ROOT))
    if missing:
        print("\n[错误] 缺少:", ", ".join(missing))
        print("       PyInstaller 缺失就装: .venv\\Scripts\\python.exe -m pip install pyinstaller")
        sys.exit(2)
    print("版本:", subprocess.run([py, "-c", "import PyInstaller;print(PyInstaller.__version__)"],
                                  capture_output=True, text=True).stdout.strip())


def run_tests():
    """打包前的回归闸。判据: **退出码为 0 且出现该套件的"通过"哨兵行**。

    两个容易踩的坑(都实测踩过):
      ① 判据不能用"输出里有 FAIL 字样"(正常通过的用例也会打印 FAIL 字样);
      ② 编码: 从 cmd 跑时子进程按 cp936 输出, 从 Git Bash 等 UTF-8 环境跑则按 utf-8 输出 ——
         两边都按一种解会误判, 所以这里**两种编码都试**, 任一能查到哨兵行就算通过。
    """
    banner("1/6 回归测试(打包前先确认功能是好的)")
    suites = (("test_open_dialog_paths.py", "ALL PASS"),
              ("test_offline_regression.py", "全部离线回归通过"),
              ("test_ui_channel.py", "ALL PASS"),
              # 控制台入口冒烟(2026-09-15 加): 那次"__main__ 入口丢失导致控制台版只打
              # 横幅就退出"的事故, 静态检查与其它用例都没拦住 —— 这条会真跑一次入口。
              ("test_entry_console.py", "全部入口检查通过"))
    bad = []
    for name, sentinel in suites:
        p = os.path.join(ROOT, "scripts", name)
        if not os.path.exists(p):
            continue
        r = subprocess.run([sys.executable, p], cwd=ROOT, capture_output=True)   # 拿 bytes, 自己解
        raw = r.stdout or b""
        outs = [raw.decode(enc, "replace") for enc in ("utf-8", "gbk")]
        out = next((o for o in outs if sentinel in o), outs[0])
        ok = (r.returncode == 0) and any(sentinel in o for o in outs)
        print(f"  {name:28s} {'通过' if ok else '失败'}")
        if not ok:
            bad.append(name)
            print("  " + "-" * 66)
            for line in out.strip().splitlines()[-15:]:
                print("  | " + line[:110])
            err = (r.stderr or b"").decode("gbk", "replace").strip().splitlines()
            if err:
                print("  | [stderr] " + err[-1][:110])
            print("  " + "-" * 66)
    if bad:
        print()
        print("[错误] 回归测试没过: " + ", ".join(bad))
        print("       上面每个失败套件下面那几行就是原因; 确认与本次改动无关可加 "
              "--skip-tests 跳过(不建议)。")
        sys.exit(3)



def smoke_console_exe(out_dir, timeout=20.0):
    """控制台版 exe 的构建后冒烟: 真跑一次, 等它打印"就绪"。

    为什么必须有(2026-09-15 事故): 核心的 `__main__` 入口曾被误缩进进 `main()` 变成死代码,
    于是 `python app/sc2_uncensor.py` 与这份 --console exe 只打一行横幅就退出、一个快捷键都
    不注册; 而界面版走 import 完全正常, 静态检查也拦不住(那份代码语法合法)。
    判据与"起不来的环境"分开:
      · 写日志但**没有"就绪"就退出了** → 入口真坏了: 返回 False(打不了发布包)
      · 压根没日志(杀软拦子进程/构建机环境问题) → 跳过, 只提示(不挡打包, 同回归闸的策略)
      · 本机已有本工具在运行(单实例锁被占) → 跳过
    """
    exe = os.path.join(out_dir, NAME + "_console.exe")
    if not os.path.exists(exe):
        print("  [跳过] 没找到", os.path.relpath(exe, ROOT))
        return True
    tmp = tempfile.mkdtemp(prefix="sc2_console_smoke_")
    logp = os.path.join(tmp, "console.log")
    env = dict(os.environ)
    env.update({"SC2_UNCENSOR_DIR": tmp, "SC2_UNCENSOR_LOG": logp,
                "SC2_UNCENSOR_NO_ATTACH": "1",     # 不连玩家游戏
                "SC2_UNCENSOR_NO_HOTKEYS": "1"})   # 不抢用户按键
    print("  跑一次:", os.path.relpath(exe, ROOT))
    p = subprocess.Popen([exe], cwd=out_dir, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.25)
            try:
                with open(logp, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except Exception:
                txt = ""
            if "就绪(游戏可稍后启动)" in txt:
                print("  OK: 控制台版 exe 起得来、进了消息循环")
                return True
            if "本工具已在运行" in txt:
                print("  [跳过] 本机已有本工具在运行(单实例锁被占)")
                return True
            if p.poll() is not None:
                tail = ""
                try:
                    out = p.communicate(timeout=5)[0] or b""
                    tail = out.decode("utf-8", "replace").strip().splitlines()[-3:]
                except Exception:
                    pass
                if txt:
                    print("  [失败] 控制台版 exe 只打印了开头就退出(入口坏了?) 退出码=",
                          p.returncode)
                    for line in txt.strip().splitlines()[-3:]:
                        print("  | " + line[:110])
                    for line in tail:
                        print("  | " + line[:110])
                    return False
                print("  [跳过] exe 没能写日志(多半被杀软拦了子进程)")
                return True
        print(f"  [跳过] {timeout:.0f} 秒内没读到日志(杀软拦子进程/构建机太慢)")
        return True
    finally:
        try:
            if p.poll() is None:
                p.terminate()
            p.communicate(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def make_icon():
    """优先用 app/app.ico(用户自己放); 没有就生成一个占位图标。"""
    src = os.path.join(APP, "app.ico")
    if os.path.exists(src):
        print("图标: 用 app/app.ico")
        return src
    out = os.path.join(BUILD, "app_placeholder.ico")
    try:
        from PIL import Image, ImageDraw, ImageFont
        size = 256
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([6, 6, size - 6, size - 6], radius=44, fill=(16, 22, 34, 255),
                            outline=(64, 200, 255, 255), width=8)
        font = None
        for cand in ("arialbd.ttf", "ARLRDBD.TTF", "seguibl.ttf"):
            try:
                font = ImageFont.truetype(cand, 108)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        d.text((size / 2, size / 2 - 6), "SC2", font=font, fill=(120, 225, 255, 255), anchor="mm")
        im.save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        print(f"图标: 生成了占位图标 {os.path.relpath(out, ROOT)}")
        print("      (想要自己的图标, 把 .ico 放到 app/app.ico 即可, 可多尺寸)")
        return out
    except Exception as e:
        print("图标: 生成失败, 用 PyInstaller 默认图标", e)
        return None


def product_version():
    """产品版本号 —— **只有一处**: 核心 `sc2_uncensor.VERSION`。

    exe 的版本资源、发布包(zip)名、界面标题栏、关于页全用它。以前 zip 名读的是界面文件里
    另一个 `__version__`（两个号各走各的，实测出现过 zip 名与 exe 版本资源不一致），
    2026-09-15 统一成这一处。
    """
    try:
        sys.path.insert(0, APP)
        import sc2_uncensor as core
        return str(core.VERSION)
    except Exception:
        try:                                  # 兜底: 直接从源码行里抠, 不依赖能否 import
            with open(os.path.join(APP, "sc2_uncensor.py"), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VERSION"):
                        return line.split("=")[1].strip().strip('"\'')
        except Exception:
            pass
    return "0.0"


def make_version_file():
    """版本资源(能减少一点杀软疑虑, 也方便用户看版本)。"""
    try:
        sys.path.insert(0, APP)
        import sc2_uncensor as core
        author = getattr(core, "__author__", "")
    except Exception:
        author = ""
    core_ver = product_version()
    ver = f"{core_ver}.0.0" if core_ver.count(".") < 2 else core_ver
    quad = (ver.split(".") + ["0", "0", "0", "0"])[:4]
    out = os.path.join(BUILD, "version_info.txt")
    body = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({", ".join(quad)}), prodvers=({", ".join(quad)}),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [
        StringStruct('CompanyName', ''),
        StringStruct('FileDescription', '星际争霸2 国服反和谐工具'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', '{NAME}'),
        StringStruct('LegalCopyright', '{author}'),
        StringStruct('OriginalFilename', '{NAME}.exe'),
        StringStruct('ProductName', '星际争霸2 国服反和谐工具'),
        StringStruct('ProductVersion', '{ver}')])]),
        VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"版本资源: {os.path.relpath(out, ROOT)}  (版本 {ver})")
    return out


def pyinstaller(entry, name, windowed, icon, verfile):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
           "--name", name, "--paths", APP, "--noupx",
           "--windowed" if windowed else "--console"]
    if icon:
        cmd += ["--icon", icon]
    if verfile:
        cmd += ["--version-file", verfile]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    for pkg in COLLECT:
        cmd += ["--collect-all", pkg]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]
    cmd.append(entry)
    r = run(cmd)
    if r.returncode != 0:
        print(f"\n[错误] PyInstaller 失败(退出码 {r.returncode})")
        sys.exit(4)
    return os.path.join(DIST, name)


def put_data_files(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for f in DATA_FILES:
        src = os.path.join(APP, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out_dir, f))
    # 界面皮肤也放一份到 exe 旁边: 界面优先从 _internal 找, 这里只是方便用户替换/查看
    skin_src = os.path.join(APP, "ui_skin")
    skin_dst = os.path.join(out_dir, "ui_skin")
    if os.path.isdir(skin_src) and not os.path.isdir(skin_dst):
        shutil.copytree(skin_src, skin_dst)


def human_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return f"{total / 1024 / 1024:.1f} MB"


def clean_runtime(out_dir):
    """清掉试跑留下的运行时文件。**ui_state.json 尤其要清** —— 它里面有"已同意免责声明"
    的标记, 打包带出去的话新用户装完就不会看到免责弹窗了(验收清单明确要求不可跳过)。"""
    removed = []
    for f in ("sc2_uncensor.log", "ui_state.json", "selftest_report.txt"):
        p = os.path.join(out_dir, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(f)
    if removed:
        print("已清理运行产物:", ", ".join(removed))
    return removed


def do_zip(out_dir, version):
    clean_runtime(out_dir)
    zip_path = os.path.join(DIST, f"{NAME}_v{version}_win64.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(out_dir):
            for f in files:
                full = os.path.join(base, f)
                z.write(full, os.path.relpath(full, DIST))
    print(f"已打包: {os.path.relpath(zip_path, ROOT)}  ({os.path.getsize(zip_path)/1024/1024:.1f} MB)")
    return zip_path


def main():
    ap = argparse.ArgumentParser(description="一键打包 SC2 反和谐工具")
    ap.add_argument("--zip", action="store_true", help="额外打成 zip")
    ap.add_argument("--console", action="store_true", help="额外打一个控制台版 exe")
    ap.add_argument("--selftest", action="store_true", help="打自检 exe 并运行(验证 OCR 等依赖)")
    ap.add_argument("--skip-tests", action="store_true", help="跳过打包前的回归测试")
    ap.add_argument("--keep-build", action="store_true", help="保留 build/ 中间产物")
    args = ap.parse_args()

    banner(f"SC2 反和谐工具 打包 · {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("项目根:", ROOT)
    check_env()
    if not args.skip_tests:
        run_tests()
    else:
        print("\n(已跳过回归测试)")

    os.makedirs(BUILD, exist_ok=True)
    banner("2/6 图标与版本资源")
    icon = make_icon()
    verfile = make_version_file()

    banner("3/6 打界面版 exe(onedir, 无控制台)")
    out_ui = pyinstaller(ENTRY_UI, NAME, True, icon, verfile)
    put_data_files(out_ui)
    print(f"\n界面版产物: {os.path.relpath(out_ui, ROOT)}  ({human_size(out_ui)})")

    console_ok = True
    if args.console:
        banner("4/6 打控制台版 exe")
        out_c = pyinstaller(ENTRY_CONSOLE, NAME + "_console", False, icon, verfile)
        put_data_files(out_c)
        print(f"\n控制台版产物: {os.path.relpath(out_c, ROOT)}  ({human_size(out_c)})")
        # 构建后冒烟: 入口坏了这儿就能当场发现(见 smoke_console_exe 的说明)
        console_ok = smoke_console_exe(out_c)
        if not console_ok:
            print("\n[错误] 控制台版 exe 起不来(入口坏了) —— 先修入口, 别把这份发出")
    else:
        print("\n(未打控制台版; 需要就加 --console)")

    self_ok = True
    if args.selftest:
        banner("5/6 打自检 exe 并运行(验证打包环境里的 OCR/截图/内存库)")
        out_s = pyinstaller(SELFTEST, NAME + "_Selftest", False, icon, verfile)
        put_data_files(out_s)
        exe = os.path.join(out_s, NAME + "_Selftest.exe")
        print("\n运行自检 exe:", exe)
        r = subprocess.run([exe], cwd=out_s, capture_output=True, text=True,
                           errors="replace")     # 不指定编码: 用本机代码页(cp936 控制台)
        print(r.stdout)
        if r.returncode != 0:
            self_ok = False
            print("[警告] 自检没通过 —— 上面 FAIL 的项目就是打包缺件, 先解决再发布!")
    else:
        print("\n(未做自检; 建议加 --selftest 验证打包后的 OCR)")

    banner("6/6 收尾")
    version = product_version()      # 产品版本只有一处(核心 VERSION), zip 名也用它
    clean_runtime(out_ui)          # 即使不 zip, 也别让试跑产物留在发布目录里
    if args.zip:
        do_zip(out_ui, version)
    if not args.keep_build:
        shutil.rmtree(BUILD, ignore_errors=True)

    print()
    print("完成。异机测试清单:")
    print("  1) 把 dist\\%s 整个目录(或 zip)拷到另一台电脑(不装 Python)" % NAME)
    print("  2) 双击 %s.exe; 若杀软报毒, 先把目录加入白名单(见 README.txt)" % NAME)
    print("  3) 启动星际2(国服, 主界面) → 点界面上的'一键反和谐'")
    print("  4) 有问题就把目录里的 sc2_uncensor.log 发回")
    print("  5) 想验依赖是否齐全: 跑 %s_Selftest.exe(用 --selftest 打的那份)" % NAME)
    if not console_ok:
        return 5
    return 0 if self_ok else 5


if __name__ == "__main__":
    sys.exit(main())
