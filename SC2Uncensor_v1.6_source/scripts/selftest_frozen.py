# -*- coding: utf-8 -*-
"""打包自检: 在**冻结后的 exe 环境**里逐项验证依赖是否齐全。

用途:
  · 打包后跑一次, 确认 OCR(winocr+winrt)、截图、内存库都真的能用(不是"能启动"就算数)
  · 用户报"打包版某个功能不对"时, 让他跑这个把输出发回来

用法(开发环境或打包后都行):
    .venv\\Scripts\\python.exe scripts\\selftest_frozen.py
    SC2Selftest.exe
"""
import os
import sys
import traceback

print("=" * 60)
print("SC2 反和谐工具 · 环境自检")
print("=" * 60)
print("frozen(是否打包环境):", bool(getattr(sys, "frozen", False)))
print("executable:", sys.executable)
print("python:", sys.version.split()[0])

ROOT = os.path.dirname(os.path.abspath(__file__))
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.abspath(os.path.join(ROOT, "..", "app")))

# 打包后数据目录 = exe 旁边; 源码运行 = app 目录
if getattr(sys, "frozen", False):
    os.environ.setdefault("SC2_UNCENSOR_DIR", os.path.dirname(os.path.abspath(sys.executable)))

results = []


def check(name, fn):
    try:
        value = fn()
        results.append((name, True, value))
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))
        if os.environ.get("SC2_SELFTEST_TRACE"):
            traceback.print_exc()


check("PIL 导入", lambda: __import__("PIL").__version__)
check("截图 ImageGrab", lambda: __import__("PIL.ImageGrab", fromlist=["x"]).grab().size)
def _blue_cluster_check():
    """蓝框识别(找"减少暴力表现"勾选框的纯像素兜底): 打包后也必须能用。
    现在不用 numpy 了(PIL 通道运算等价实现, 省 ~25MB), 所以这里真造一张图验一遍。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (80, 60), (20, 24, 32))
    ImageDraw.Draw(im).rectangle([10, 12, 28, 30], outline=(80, 170, 255), width=2)
    import sc2_uncensor
    got = sc2_uncensor._blue_clusters(im)
    assert len(got) == 1, f"应识别出 1 个蓝框, 实际 {got}"
    return f"1 个蓝框 @ {got[0]}"


check("蓝框识别(勾选框兜底)", _blue_cluster_check)
check("psutil", lambda: f"{len(__import__('psutil').pids())} 个进程")
check("pymem(内存读写)", lambda: "ok" if __import__("pymem").Pymem else "ok")
check("winocr 导入", lambda: getattr(__import__("winocr"), "__name__", "winocr"))
check("winrt 核心模块", lambda: ", ".join(sorted(
    m for m in ("winrt._winrt", "winrt.windows.media.ocr", "winrt.windows.globalization",
                "winrt.windows.graphics.imaging", "winrt.windows.storage.streams")
    if __import__(m, fromlist=["x"]) or True)))

# 核心模块(会创建/读取数据目录里的 config.json)
try:
    import sc2_uncensor as core
    results.append(("核心模块导入", True, f"v{core.VERSION} | 数据目录={core.BASE_DIR}"))
except Exception as e:
    results.append(("核心模块导入", False, f"{type(e).__name__}: {e}"))
    core = None

if core is not None:
    check("offsets.dat 可读", lambda: f"{len(core._load_offsets_override())} 个版本条目")
    check("OCR 的 ps1 兜底脚本在", lambda: core._ocr_ps1_path())
    check("SC2 窗口检测", lambda: ("找到窗口" if core.find_sc2_hwnd() else "游戏没开(正常)"))
    # 最关键的一项: 真跑一次屏幕 OCR —— 走的正是核心生产路径(winocr -> winrt)
    def _real_ocr():
        img = core.grab_retry()
        res = core._ocr_recognize(img)
        n = len(res.get("lines", []))
        return f"识别 {n} 行(屏幕文字越多行数越多)"
    check("屏幕 OCR(真跑一次)", _real_ocr)
    # OCR 缩放链路(核心按 scale=2 放大后再识别)
    check("OCR 放大链路(scale=2)", lambda: f"{len(core.ocr_center(scale=2)[0])} 行")

print()
lines = []
ok_all = True
for name, ok, value in results:
    line = f"[{'OK ' if ok else 'FAIL'}] {name:22s} {value}"
    print(line)
    lines.append(line)
    ok_all = ok_all and ok

verdict = ("自检通过: 这个环境下依赖齐全, OCR 可用。"
           if ok_all else
           "自检失败: 上面标 FAIL 的项目就是缺件, 把这份输出(或 selftest_report.txt)发回即可定位。")
print()
print(verdict)

# 报告落盘: 双击运行时窗口会一闪而过, 有文件才方便发回来
try:
    report = os.path.join(os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__)), "selftest_report.txt")
    NL = chr(10)                       # 不写字面量转义, 免得被工具链吞掉
    with open(report, "w", encoding="utf-8") as f:
        f.write("SC2 反和谐工具 · 环境自检报告" + NL)
        f.write("时间: " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S") + NL)
        f.write("frozen: " + str(bool(getattr(sys, "frozen", False))) + NL)
        f.write("exe: " + sys.executable + NL)
        f.write("python: " + sys.version.split()[0] + NL + NL)
        f.write(NL.join(lines) + NL + NL + verdict + NL)
    print("报告已保存:", report)
except Exception as e:
    print("(报告落盘失败:", e, ")")

# 双击运行时是"跑完就关"的窗口, 停一下让人能看完
try:
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        input("按回车键关闭...")
except Exception:
    pass
sys.exit(0 if ok_all else 1)
