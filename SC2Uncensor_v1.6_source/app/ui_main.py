# -*- coding: utf-8 -*-
"""
星际争霸2 国服反和谐工具 · 图形界面（app/ui_main.py）

界面只做壳：内存、OCR、点击、快捷键逻辑全部在 core（app/sc2_uncensor.py）里，
这里一行偏移都不碰，也不自己发声（声音由核心统一负责，一次操作只响一声）。

线程模型（务必别改坏）：
  · Tk 主线程   —— 窗口、控件、轮询结果刷新。**任何控件都只能在这里碰**
  · 快捷键线程     —— core.main() 跑在里面（注册全局快捷键/按键转发/看门狗）
  · 操作线程     —— 界面点按钮时起一个临时线程，只做一件事：
                     core.post_ui_call(fn) 把流程投递到快捷键线程执行、等结果，
                     再把结果丢进队列给 Tk 主线程。这样界面不会卡（流程要 2 秒），
                     而且界面操作与快捷键操作天然串行（不会两个 OCR 流程互相抢前台）。

窗口形态（2026-09-14 用户实测反馈后改过一次，别改回去）：
  主窗口是"普通窗口 + 用 Win32 去掉原生标题栏和边框"，**保留 WS_SYSMENU/WS_MINIMIZEBOX**。
  早期用 tk 的 overrideredirect(True)（= WS_POPUP）：最小化会退化成桌面左下角一个
  没有任务栏按钮的小方块，任务栏里也再找不到它。弹窗则统一自绘标题栏，免得"上面一条
  系统白色标题栏、下面是深色界面"的风格割裂。

界面自身的状态（窗口位置/展开项/免责同意）存 app/ui_state.json；
核心的配置（快捷键/开关）仍在 app/config.json —— 两个文件各管各的，互不覆盖。
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import ui_text as TX             # ★ 界面文案都集中在这个文件里(想改字就改它)

# 版本号**只有一处**: 核心的 sc2_uncensor.VERSION(界面标题栏、关于页、exe 版本资源、
# 发布包名 全用它)。这里原来还写过一个界面自己的 __version__(打包时和核心版本一起塞进
# exe 的版本资源, 于是同一个 exe 上出现两个号) —— 2026-09-15 去掉, 免得改一处漏一处。
APP_TITLE = TX.APP_TITLE
_HERE = os.path.dirname(os.path.abspath(__file__))


# ==================== 环境准备（必须在 import 核心之前） ====================
def _data_dir():
    """数据目录：打包后 = exe 所在目录（用户能直接改到 config.json）；
    源码运行 = app 目录。核心用环境变量认这个目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _HERE


DATA_DIR = _data_dir()
os.environ.setdefault("SC2_UNCENSOR_DIR", DATA_DIR)
DATA_DIR = os.environ["SC2_UNCENSOR_DIR"]     # 认外部重定向(打包/测试/用户自定义)
UI_STATE_PATH = (os.environ.get("SC2_UNCENSOR_UI_STATE")
                 or os.path.join(DATA_DIR, "ui_state.json"))


class _NullStream:
    """pythonw / 打包成 windowed 时 stdout 是 None，给它一个能吃的对象，
    免得核心的 print 在半路抛异常。"""

    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullStream()
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import sc2_uncensor as core      # noqa: E402  核心（导入时会开日志文件、打横幅）
import ui_theme as T             # noqa: E402



# ==================== 提权 / 重启（不经过 PowerShell，UAC 秒弹） ====================
RESTART_FLAG = "--restart"      # 提权/重启拉起的新实例带这个参数(命令行参数能过 UAC, 环境变量不能)


def elevate_params(extra=RESTART_FLAG):
    """拼提权启动的参数串（源码态要带脚本路径, 打包态不用）。

    这里**自己给路径加引号**：目录名可能含空格（本仓库就叫 "starcraft2 uncensor"），
    PowerShell 的 -ArgumentList 数组不给含空格元素加引号、pythonw 会拿着半个路径静默退出
    （踩过，见 docs/UI说明.md §10.4）。走 ShellExecuteW 的 lpParameters 由我们自己拼，
    所以必须自己保证引号。
    """
    if getattr(sys, "frozen", False):
        return (extra or "").strip()
    return (f'"{os.path.abspath(__file__)}" {extra}').strip()


def ancestor_pids(depth=5):
    """自己的父进程链（含父、祖父…）。

    必须排除它们：venv 的 python/pythonw.exe 是个"转发器"，会再起一个真解释器，
    于是**自己的父进程命令行里也有 ui_main.py**，会被误判成"另一个实例"而白等一场
    （实测把重启卡到超时）。
    """
    out = set()
    try:
        import psutil
        proc = psutil.Process()
        for _ in range(depth):
            parent = proc.parent()
            if parent is None:
                break
            out.add(parent.pid)
            proc = parent
    except Exception:
        pass
    out.add(os.getppid())
    return out


def _author_brief():
    """标题栏署名: 每位作者取简称(地方窄, 长名字会撞版本号); 悬停能看完整署名。

    简称映射表在 ui_text.AUTHOR_ABBREV(没映射的原样显示, 如"水本")。
    """
    try:
        names = [n.strip() for n in str(getattr(core, "__author__", "")).split("、") if n.strip()]
    except Exception:
        names = []
    return "、".join(TX.AUTHOR_ABBREV.get(n, n) for n in names) or "作者"


def _author_full():
    """完整署名(悬停提示/程序横幅用)"""
    try:
        return str(getattr(core, "__author__", "")).strip() or "作者"
    except Exception:
        return "作者"


def other_instances():
    """还有哪些本工具的实例在跑（排除自己与自己的父进程链）。返回 [(pid, 名字), ...]

    匹配要**同时**满足"是 Python 进程（或打包的 exe）"和"命令行里有 ui_main.py"：
    只按字符串找会把任何命令行里恰好出现 ui_main 字样的进程都算进来（实测被自己的
    测试 shell 骗过，白等一场）。
    """
    me = os.getpid()
    skip = ancestor_pids() | {me}
    out = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["pid"] in skip:
                    continue
                nm = (proc.info.get("name") or "")
                low = nm.lower()
                packed = low.startswith("sc2uncensor")
                if not packed and low not in ("python.exe", "pythonw.exe"):
                    continue                     # 不是本工具的解释器/打包 exe
                cl = " ".join(proc.info.get("cmdline") or [])
                if not packed and "ui_main.py" not in cl:
                    continue
                out.append((proc.info["pid"], nm))
            except Exception:
                continue
    except Exception:
        pass
    return out


def precheck_elevate():
    """启动最前面做的"要不要提权"检查。

    故意**不 import 核心**：核心要拉 pymem/PIL/winocr 一堆东西，得 1~2 秒；
    用户双击 exe(源码版是 bat) 后希望 UAC 尽快弹出来，所以这里只用 json 读一下 config.json。
    需要提权就立刻 runas 拉起新实例（带 --restart）并让本进程退出。
    返回 True = 已经交给提权实例了，调用方应直接退出。
    """
    if os.environ.get("SC2_UNCENSOR_UI_NO_ELEVATE") or RESTART_FLAG in sys.argv:
        return False
    try:
        import json as _json
        cfg = _json.load(open(os.path.join(DATA_DIR, "config.json"), encoding="utf-8-sig"))
        if not cfg.get("admin"):
            return False
    except Exception:
        return False
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
        exe = sys.executable
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, elevate_params(), DATA_DIR, 1)
        if rc > 32:
            print("[界面] config admin=true: 已请求以管理员身份重新启动")
            return True
        print("[界面] 提权被取消, 继续以普通权限运行")
    except Exception as e:
        print(f"[界面] 提权启动失败, 继续以普通权限运行: {e}")
    return False


# ==================== 文案（★ 想改界面上的字，去 app/ui_text.py 改） ====================
OP_LABEL = TX.OP_LABEL
BUSY_TEXT = TX.BUSY_TEXT
OP_STATE = TX.OP_STATE
TERMS = TX.TERMS
DISCLAIMER = TX.DISCLAIMER
ANTIVIRUS_TIP = TX.ANTIVIRUS_TIP
HINT = TX.HINT
LAMP_TIPS = TX.LAMP_TIPS
NOTICE = TX.NOTICE
TOOLBAR = TX.TOOLBAR
BTN_PAUSED_KEY = TX.BTN_PAUSED_KEY
MASTER_KEY_FMT = TX.MASTER_KEY_FMT
MASTER_STATE = TX.MASTER_STATE
CONNECT_BEEP = TX.CONNECT_BEEP      # 游戏连接提示音(设置里的开关)
QUICKSTART_TITLE = TX.QUICKSTART_TITLE
QUICKSTART_EXPAND = TX.QUICKSTART_EXPAND      # 收起时右侧提示("展开详细说明")
QUICKSTART_COLLAPSE = TX.QUICKSTART_COLLAPSE  # 展开时右侧提示("收起")
QUICKSTART_HEAD = TX.QUICKSTART_HEAD          # 收起时那一行(带 {} 当前快捷键占位)
QUICKSTART_HL = TX.QUICKSTART_HL              # 收起行里要标醒目色的片段
QUICKSTART = TX.QUICKSTART          # 展开后的逐条详细说明(改字去 ui_text)
TABS = TX.TABS                      # 横向页签: [(键, 标题)]
TAB_TIP = TX.TAB_TIP
BOTTOM_BTNS = TX.BOTTOM_BTNS        # 底部常驻三个按钮: (文字, 悬停说明, 动作名)
CONN_HINT = TX.CONN_HINT            # 连接状态行右边那句初次使用引导
CONN_HINT_TIP = TX.CONN_HINT_TIP
SETTING_TIPS = TX.SETTING_TIPS      # 「设置」页各勾选项的白话说明(别再用 config 的 _说明)
HINT_LIT = TX.HINT_LIT              # 提示区里那些零散的一句话(别在 _set_hint 里写死中文)

OP_ORDER = ["uncensor", "uncensor_auto", "restore", "restore_auto", "status", "hotkey_master"]
# 主操作区只留 手动模式反和谐/自动模式反和谐/快捷键总开关，其余进「更多操作」
MORE_ITEMS = ["restore", "restore_auto", "click_toggle", "status"]
# 注: BUSY_TEXT / OP_STATE 等文案**只在 ui_text.py 里定义**。这里曾把它们又抄了一份,
#     结果 ui_text.py 那份被遮蔽 —— 照着"改文案只改 ui_text.py"去改会发现不生效。
#     文案一律只从 TX 引用(上面的 import), 不要再在本文件里重新赋值。
#
# 「选项操作」灯讲的是**本次运行里"那一下点击"做过没有**, 不是内存里的值——
# 所以"选项状态"变好看了也不代表生效, 两栏必须分开看(用户实测反馈)。


# 句末标点: 源文里凡是**以这些结尾**的行, 都是"一个说法讲完了", 下一行该另起一段
_SENT_END = "。！？；：!?;:"


# 中文禁则: 这些标点不能出现在行首(会被"挤"在上一行末) / 不能出现在行尾
_KIN_NO_START = "、。，；：？！）】》」』%…～" 
_KIN_NO_END = "（【《「『"


def _text_avail(txt, width, scale):
    """弹窗正文**真正画得下**的宽度 + 允许标点悬挂的余量(都是物理像素)。

    折行断点全靠这两个数, 所以必须算准: 算大了, 关掉自动折行的正文(wrap="none")会把右边
    超出的字**直接裁掉**(悬挂的标点最容易被裁半个); 算小了白丢一截, 尾行孤字变多
    (用户实测反馈过"只有两个字符跑到下一行了"、"感觉现在的宽度可以放下")。

    真实宽度 = 弹窗宽 - 1px 亮边框 - 左右各 14px 内缩 - 竖向滚动条 - 文本框自己的
    边框/高亮圈/内边距。滚动条宽度**问控件**(ttk 主题不同宽度不一样), 问不到按 15 算。
    再留 4px 安全边 = 标点最多悬挂出界 4px, 一定还在框内。
    """
    sb = 15
    try:
        for ch in txt.master.winfo_children():
            if ch is not txt and ch.winfo_class() == "TScrollbar":
                sb = max(sb, int(ch.winfo_reqwidth()))
    except Exception:
        pass
    inner = (int(width * scale) - 2 - 2 * int(14 * scale) - sb
             - 2 * int(txt.cget("highlightthickness")) - 2 * int(txt.cget("bd"))
             - 2 * int(txt.cget("padx")))
    return max(120, inner - 4), 4


def _tail_start(cur):
    """折行时"该从哪儿切": 返回切点下标 —— 末尾挂着的标点一起走, 再往前带**一个普通字**,
    这样下一行开头一定不是标点(中文禁则的"标点不落行首")。

    cur 短到没有普通字可带(整行都是标点, 罕见)时返回 None, 表示"没法这么切"。
    """
    k = len(cur)
    while k > 0 and cur[k - 1] in _KIN_NO_START:
        k -= 1
    k -= 1                    # 再带一个普通字
    return k if k > 0 else None


def _wrap_cjk(text, measure, width, hang=0):
    """按像素宽度折行 + 简单中文禁则, 返回用 
 接起来的文本。

    为什么要自己折: Tk 的 Text 只有 word/char 两种折行 —— word 只在空格处断(中文段落没有
    空格 → 标点会被单独挤到下一行), char 逐字断虽整齐但没有禁则, 同样会出现"；"孤零零
    一行。中文排版必须"标点不落行首", 所以这里按字累加量宽, 到边界时按禁则选断点。
    measure(字符串) 返回像素宽; width 是可用像素宽。

    hang = 允许"标点悬挂"出界的像素数(右边缘留的那点余量)。标点放不下、悬挂余量又不够时,
    改成把**前面那个字一起挪到下一行** —— 宁可这一行短一个字, 也不能让标点落行首、更不能
    让它被 wrap="none"(见 _fill_blocks)直接裁掉半截。
    """
    lines, cur = [], ""
    for ch in text:
        if ch == chr(10):
            lines.append(cur)
            cur = ""
            continue
        if cur and measure(cur + ch) > width:
            if ch in _KIN_NO_START and measure(cur + ch) <= width + hang:
                cur += ch                   # ① 标点悬挂在右边缘(出界量在悬挂余量内, 不会被裁)
            elif ch in _KIN_NO_START and _tail_start(cur) is not None:
                # ② 悬挂余量不够放这个标点: 把它**连同它前面那个字**(末尾已经挂着的标点
                #    也一起)挪到下一行 —— 宁可这一行短一个字, 也不能让标点落行首,
                #    更不能让它被 wrap="none" 裁掉半截
                k = _tail_start(cur)
                lines.append(cur[:k])
                cur = cur[k:] + ch
            elif cur[-1] in _KIN_NO_END:
                lines.append(cur[:-1])      # ③ "（"这类不能行尾 → 跟下一行一起挪
                cur = cur[-1] + ch
            else:
                lines.append(cur)           # ④ 普通断行
                cur = ch
            continue
        cur += ch
    lines.append(cur)
    return chr(10).join(lines)


def _mix(rgb0, rgb1, t):
    """两个颜色之间取插值(t=0 → rgb0, t=1 → rgb1), 返回 #rrggbb"""
    return "#%02x%02x%02x" % tuple(
        int(round(rgb0[k] + (rgb1[k] - rgb0[k]) * t)) for k in range(3))


def _grad_v(canvas, x0, y0, x1, y1, rgb_top, rgb_bottom, tags=("tab",), ease=None):
    """在画布上画一块**竖向渐变**(逐行铺色)。用在小面积控件上(页签底), 开销可忽略。

    ease: None=匀速; 数字 = 指数(t**ease, 越大"底色出现得越晚"); "fsf" = **快-慢-快**
    （t + sin(2πt)/2π）—— 页签底部那条按用户要求用这个: 两头变得快、中段慢。
    """
    n = max(1, int(y1 - y0))
    for i in range(n):
        t = i / max(1, n - 1)
        if ease == "fsf":
            t = t + math.sin(2 * math.pi * t) / (2 * math.pi)
        elif ease:
            t = t ** ease
        canvas.create_line(x0, y0 + i, x1, y0 + i,
                           fill=_mix(rgb_top, rgb_bottom, t), tags=tags)


def _reflow_doc(text):
    """把"按控制台排版"的文档重新折成适合弹窗的段落。返回 [(文本, 类型)]。

    类型: "head"(【章节标题】) / "body"(缩进正文, 弹窗里做左缩进) / "plain"(顶格行) / ""(空行)。

    规则(按用户对源文的理解):
      · 源文里的换行有**两种**: 一种是"怕一行排不下"的宽度换行(行尾是逗号或没有标点),
        在弹窗里应当**合并**(宽度不同, 换行点本来就该重排);
        另一种是"另一个条目/另一句话"(行尾是句号、分号、冒号等), 应当**保留换行**。
        混在一起就会显得杂乱 —— 所以按行尾标点决定合不合并。
      · 合并时若接缝两侧有 ASCII 字符, 补一个空格(否则 "…(NOP+暴露),然后…" 会挤在一起)。
      · 缩进: 源文里缩进过的段落(章节正文、列表项续行)在弹窗里给 lmargin 左缩进,
        观感对齐 bat(用户按 bat 的排版要求)。
    """
    blocks, cur, cur_ind = [], [], False

    def flush():
        if cur:
            txt = cur[0]
            for prev, nxt in zip(cur, cur[1:]):
                joiner = " " if (prev[-1:].isascii() or nxt[:1].isascii()) else ""
                txt += joiner + nxt
            is_item = txt[:1] in "·•★☆" or txt[:1].isdigit() or (
                len(txt) > 1 and "①" <= txt[0] <= "⑳")
            if txt.startswith("【"):
                kind = "head"
            elif txt[:1] == "※":
                # "※"开头那几句(免责/仅限PvE)是**整篇的前提**, 顶格显示 —— 缩进成正文层次
                # 就看着像"正文的一部分"了(用户实测反馈: 使用说明里免责那段不该跟正文对齐)
                kind = "plain"
            else:
                kind = "body" if (cur_ind or is_item) else "plain"
            blocks.append((txt, kind))
            cur.clear()

    for raw in (text or "").splitlines():
        # "· 内容" / "① 内容" 这类标记后的空格会让 Tk 把标记自己折成一行(实测: 弹窗里出现
        # 孤零零一个"·"), 收掉空格; "注: " 同理收成全角冒号
        line = re.sub(r"^([·•★☆①-⑳])\s+", lambda m: m.group(1) + " ", raw.strip())
        line = line.replace("注: ", "注：")
        if not line:
            flush()
            blocks.append(("", ""))                   # 空行 = 段落分隔
            continue
        if len(line) >= 4 and set(line) <= set("=-~—_*"):
            flush()                                   # 纯装饰线(=====): 弹窗里没用
            continue
        starts_new = (not cur) or (cur[-1][-1:] in _SENT_END)
        if line.startswith("【"):
            flush()                                   # 章节标题独占一段
            blocks.append((line, "head"))
            cur_ind = False
            continue
        if line[0] in "·★①※" or line[:1].isdigit():
            starts_new = True                         # 条目行
        if starts_new:
            flush()
            cur_ind = raw[:1].isspace()
        elif raw[:1].isspace():
            cur_ind = True                            # 续行本身缩进 → 整段左缩进
        cur.append(line)
    flush()
    out = []
    for text_, kind in blocks:
        if text_ == "" and (not out or out[-1][0] == ""):
            continue
        out.append((text_, kind))
    return out


def _cfg_tip(key):
    """配置项的中文说明取 config.json 的 _说明；去掉开头的编号
    （"1手动模式…"里的那个 1 只是写配置文件时方便看的，界面上一条条列出来就不需要了）。"""
    try:
        t = core.CFG.get("_说明", {}).get(key, "") or ""
    except Exception:
        return ""
    import re
    return re.sub(r"^\s*\d+\s*", "", t).strip()


# ==================== ui_state.json ====================
DEFAULT_STATE = {
    "pos": None,
    "tab": "more",       # 当前页签: more/hotkeys/settings/log
    "quick": False,       # 「快速上手」是否展开
    "wait_game": False,
    "disclaimer": None,       # {"accepted": bool, "version": str, "time": str}
    "antivirus_seen": False,
}


def load_state():
    st = dict(DEFAULT_STATE)
    try:
        with open(UI_STATE_PATH, encoding="utf-8-sig") as f:
            st.update(json.load(f) or {})
    except Exception:
        pass
    return st


def save_state(st):
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==================== 日志接管（核心的 print → 日志面板） ====================
LOGQ = queue.Queue()


class _UiTee:
    """包住核心装好的 sys.stdout：文件日志照旧（先转发），同时把整行送进界面队列。

    必须尽早装上：核心 main() 的启动横幅、就绪提示、每次操作的日志都要进面板。
    """

    def __init__(self, real, q):
        self.real, self.q = real, q
        self._buf = ""

    def write(self, s):
        try:
            self.real.write(s)
        except Exception:
            pass
        try:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self.q.put(("log", line))
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass

    def isatty(self):
        return False


sys.stdout = _UiTee(sys.stdout, LOGQ)


# ==================== 弹窗基础设施 ====================
def keep_above(win, parent=None):
    """弹窗别被主窗口盖住：主窗口置顶时（或自动化截图时）弹窗也跟着置顶。

    不无脑置顶——否则每点一次"修改"弹窗都会浮在所有软件之上，很烦。
    """
    try:
        if parent is not None and parent.attributes("-topmost"):
            win.attributes("-topmost", True)
    except Exception:
        pass
    if os.environ.get("SC2_UNCENSOR_UI_TEST"):
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass


_SKIN = None                   # 由 App 建好后赋值（弹窗按钮要用同一套皮肤）
BTN_GAP = 10                   # 弹窗里按钮之间的间距（逻辑像素；5 太小, 看着像贴在一起）
_TK_TID = None                 # Tk 主线程 id(App 建窗口那一步记下)


def _require_tk_thread(what):
    """Tk 控件只能由"建窗口的那个线程"碰 —— 与核心的 _require_hotkey_thread 同一个套路。

    为什么要有这道护栏: 这条红线以前只写在文件头注释里, 结果 _start_core 的 check()
    在工作线程里踩了它(核心比 mainloop 先就绪时抛 RuntimeError, 而且**静默失效**,
    因为异常发生在工作线程里, 界面看着什么都不会发生)。现在改成调用点直接报错,
    谁再写错就当场炸在开发机上, 不会变成偶发怪毛病。
    工作线程要动界面: `self.q.put((...))` 投给 _pump, 在 Tk 线程里执行。
    """
    if _TK_TID is not None and threading.get_ident() != _TK_TID:
        raise RuntimeError(
            f"{what} 只能在 Tk 主线程调用(当前线程 {threading.get_ident()} != {_TK_TID})。"
            f"工作线程要动界面, 请投队列(self.q.put(...)) 交给 _pump 执行")


class _Stack:
    """纵向行布局：按顺序算 y，别写死一串坐标。

    用法：st = _Stack(y0, gap=10)；y = st.row(高度) 返回这一行的 y 并把游标下移；
    st.sep(gap) 只下移（用于画分隔线）。这样"改窗口高度/改某个组件高度"时，
    后面的行、分隔线、折叠区都会自动跟着走——项目里明确要能继续调布局与美术。
    """

    def __init__(self, y0, gap=10):
        self.y = int(y0)
        self.gap = int(gap)

    def row(self, height, gap=None):
        y = self.y
        self.y += int(height) + (self.gap if gap is None else int(gap))
        return y

    def sep(self, gap=8):
        y = self.y
        self.y += int(gap)
        return y


def bind_check_key(cb):
    """勾选框的键盘：空格**和回车**都能切换。

    为什么自己绑：Tk 的 Checkbutton 只给空格做了绑定，回车按下去一点反应都没有
    （用户实测反馈"好多地方按回车没反应"）。invoke() 与鼠标点一下完全等价——改变量的
    同时会跑 command，所以不会出现"值变了但 command 没跑"的半截状态。
    返回 "break" 是为了不让它再冒到弹窗的"默认按钮"上（否则一下回车既取消勾选、又提交）。
    """
    def hit(_e=None):
        try:
            if str(cb.cget("state")) == "disabled":
                return "break"
            cb.invoke()
        except Exception:
            pass
        return "break"
    cb.bind("<space>", hit)
    cb.bind("<Return>", hit)
    return cb


class DialogBase(tk.Toplevel):
    """统一风格的深色弹窗：自绘标题栏(可拖动) + 关闭叉 + 内容区 + 底部按钮排。

    为什么自绘标题栏：主窗口是无边框皮肤窗口，弹窗要是还顶着系统那条白色标题栏，
    风格就割裂了（用户实测反馈）。按钮**点完自动关窗**——之前有几处回调写成
    `lambda: None`，点了没反应（用户反馈"知道了/关闭 没用"）。
    """

    def __init__(self, master, title, scale=1.0, width=520, auto_close=True, tag="弹窗"):
        super().__init__(master, bg=T.C["metal_light"])
        self.scale, self.auto_close, self.dlg_tag = scale, auto_close, tag
        self.W = width
        self.master_win = master
        self.withdraw()
        self.title(title)
        self.overrideredirect(True)          # 弹窗不需要任务栏/最小化，直接无边框自绘
        s = scale
        inner = tk.Frame(self, bg=T.C["bg"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)     # 1px 亮边框
        self.head = tk.Canvas(inner, height=int(34 * s), bg=T.C["panel"],
                              highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        self.head.create_text(int(14 * s), int(17 * s), anchor="w", text=title,
                              fill=T.C["text"], font=T.font(11, True, s))
        self.close_item = self.head.create_text(
            int((width - 22) * s), int(17 * s), text="✕", fill=T.C["text"],
            font=T.font(13, scale=s), tags=("close",))
        self.head.tag_bind("close", "<Enter>",
                           lambda _e: self.head.itemconfigure(self.close_item, fill=T.C["bad"]))
        self.head.tag_bind("close", "<Leave>",
                           lambda _e: self.head.itemconfigure(self.close_item, fill=T.C["text"]))
        self.head.tag_bind("close", "<Button-1>", lambda _e: self._on_close())
        self.head.create_line(0, int(33 * s), int(width * s), int(33 * s),
                              fill=T.C["metal_light"])
        T.WindowDragger(self, self.head)
        self.head.bind("<Button-1>", lambda _e: self.lift(), add="+")
        self.body = tk.Frame(inner, bg=T.C["bg"])
        self.body.pack(fill="both", expand=True, padx=int(14 * s), pady=(int(10 * s), 0))
        self.bar = tk.Frame(inner, bg=T.C["bg"])
        self.bar.pack(fill="x", padx=int(14 * s), pady=int(12 * s))
        self._btns = []
        self._primary_idx = None
        self._on_close_cb = None
        # 键盘: 回车 = 默认按钮(焦点在正文/标题栏上时回车也有去处, 不再"按了没反应"),
        # Esc = 关窗(等于点标题栏那个 ✕)。焦点在按钮/勾选框上时它们自己的绑定先吃掉
        # 并返回 "break", 不会一下触发两次。
        self.bind("<Return>", self._on_return)
        self.bind("<Escape>", self._on_escape)

    # ---- 内容 ----
    def add_buttons(self, buttons):
        """buttons: [(文字, 回调, 是否主按钮)] —— 按等宽列居中分散排布。

        档位按"每列能分到多宽"选：窄弹窗(改键弹窗)里塞 240 宽的按钮会被切掉
        （用户实测："确定并生效/取消 有些大了 按钮被遮挡"）。
        """
        cols = max(1, len(buttons))
        avail = (self.W - 28 - 2 * BTN_GAP * cols) / cols   # 28=左右留白, BTN_GAP=按钮两侧间距
        kind = "small"
        for w, k in ((240, "big"), (160, "wide"), (88, "small")):
            if w <= avail:
                kind = k
                break
        for i, (text, cb, _primary) in enumerate(buttons):
            if _primary and self._primary_idx is None:
                self._primary_idx = i          # 回车触发的"默认按钮"(见 _default_button)
            # 主按钮用强调色+加粗画出来: 一是主次分明, 二是"回车会按哪个"看得出来
            b = T.ImgButton(self.bar, text, kind=kind, command=lambda c=cb: self._hit(c),
                            skin=_SKIN, scale=self.scale, bg=T.C["bg"],
                            accent=bool(_primary))
            self.bar.columnconfigure(i, weight=1, uniform="dlgbtn")
            b.grid(row=0, column=i, padx=int(BTN_GAP * self.scale))
            self._btns.append(b)
        return self._btns

    # ---- 键盘: 回车/取消(见 __init__ 里的绑定) ----
    def _default_button(self):
        """当前"回车该按哪个": add_buttons 里标了主按钮的那一枚; 禁用时回车什么都不做
        (免责弹窗"同意并继续"没勾选时就是灰的 —— 回车不该能绕过去)。"""
        i = self._primary_idx
        if i is None or not (0 <= i < len(self._btns)):
            return None
        b = self._btns[i]
        return b if b.enabled else None

    def _on_return(self, _e=None):
        b = self._default_button()
        if b is None:
            return "break"
        b.command()               # ImgButton 的命令已包成"跑回调 + 关窗", 与点一下等价
        return "break"

    def _on_escape(self, _e=None):
        self._on_close()          # 有 close 回调就走回调(免责弹窗=不同意退出), 否则直接关
        return "break"

    def _hit(self, cb):
        keep = False
        try:
            cb()
            keep = getattr(self, "_keep_open", False)   # 回调里可要求"别关窗"
        finally:
            self._keep_open = False
            if self.auto_close and not keep and self.winfo_exists():
                self.destroy()

    def _on_close(self):
        if self._on_close_cb:
            self._on_close_cb()
        else:
            self.destroy()

    def set_close_cb(self, cb):
        self._on_close_cb = cb

    def set_enabled(self, idx, on):
        if 0 <= idx < len(self._btns):
            self._btns[idx].set_enabled(on)

    # ---- 定位/显示 ----
    def finish(self, width=None, height=None, x=None, y=None, announce=True):
        width = width or self.W
        s = self.scale
        self.update_idletasks()
        need = self.winfo_reqheight()                 # 按内容算高度, 免得高缩放下按钮被切
        h = max(int((height or 0) * s), need)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if (x is None or y is None) and getattr(self, "_pos", None):
            x, y = self._pos                         # 第二次调(fit_content)时别又挪个位置
        if x is None or y is None:
            mx = self.master_win.winfo_rootx() if self.master_win is not None else 0
            my = self.master_win.winfo_rooty() if self.master_win is not None else 0
            x, y = mx + int(40 * s), my + int(50 * s)
        w = int(width * s)
        x, y = max(0, min(x, sw - w)), max(0, min(y, sh - h))
        self._pos = (x, y)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        for step in (self.grab_set, self.lift, self.focus_force):
            try:
                step()
            except Exception:
                pass
        keep_above(self, self.master_win)
        # 延迟报尺寸：窗口管理器异步应用, 刚 set 完就读会读到旧值(自动化截图要用它定位)。
        # 打**弹窗自己的名字**并写明这两段数字各是什么 —— 原来只打一句
        # "[界面] 弹窗 675x434+660+64", 用户看不出哪个是尺寸、哪个是位置(实测反馈)。
        self.after(300, lambda: print(
            f"[界面] {self.dlg_tag}「{self.title()}」 "
            f"{self.winfo_width()}x{self.winfo_height()}"
            f"+{self.winfo_x()}+{self.winfo_y()}（宽x高 + 左上角x+y）"))


class Notice(DialogBase):
    """通用说明弹窗：标题 + 可滚动正文 + 可选勾选框 + 按钮排。"""

    def __init__(self, master, title, body, buttons, scale=1.0, width=520, height=420,
                 check=None, auto_close=True, on_close=None, tag="弹窗"):
        super().__init__(master, title, scale, width, auto_close=auto_close, tag=tag)
        s = scale
        sb = ttk.Scrollbar(self.body, style="TScrollbar", orient="vertical")
        # 正文用 char 折行: Tk 的 word 折行只在空格处断, 而中文段落里没有空格 → 长中文串
        # 会把标点/列表标记单独挤成一行(实测:"·"和"；"孤零零一行)。char 折行逐字断, 中文
        # 段落才整齐(英文单词的换行由 _reflow_doc 前的空格保留, 见下)。
        txt = tk.Text(self.body, wrap="char", bg=T.C["metal_dark"], fg=T.C["text"],
                      font=T.font(10, scale=s), relief="flat", padx=int(10 * s),
                      pady=int(8 * s), insertwidth=0, highlightthickness=1,
                      highlightbackground=T.C["metal_light"], highlightcolor=T.C["neon"],
                      height=max(6, int(height / 30)))
        txt.insert("1.0", body)
        txt.configure(state="disabled")
        self.txt = txt          # 挂出来给自动化用例看排版(检查正文有没有被硬换行切碎)
        self.check_var = tk.BooleanVar(value=bool(check[1]) if check else False)
        # 勾选框必须先 pack(side="bottom")：expand 的文本框会吃掉全部剩余空间，
        # 后 pack 的勾选框会被挤成 0 高（免责弹窗里等于用户点不到"同意"，踩过）
        if check:
            self.chk = tk.Checkbutton(
                self.body, text=check[0], variable=self.check_var,
                command=check[2] if len(check) > 2 else None,
                bg=T.C["bg"], fg=T.C["text"], selectcolor=T.C["metal_dark"],
                activebackground=T.C["bg"], activeforeground=T.C["neon"],
                font=T.font(11, scale=s), anchor="w",
                highlightthickness=1, highlightbackground=T.C["bg"],
                highlightcolor=T.C["neon"], bd=0)
            bind_check_key(self.chk)          # 空格/回车都能勾(见 bind_check_key)
            self.chk.pack(side="bottom", fill="x", pady=(int(8 * s), 0))
        txt.configure(yscrollcommand=sb.set)
        sb.configure(command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        if on_close:
            self.set_close_cb(on_close)
        self.add_buttons(buttons)
        if body:
            # 正文构造时就给全了(首启动那个免责弹窗): 直接按内容收高度, 底下别留一大片空白
            self._shrink_text()
            self.finish(width, 0)
        else:
            # 正文是构造完才填的(见 App._fill_blocks): 先用设定高度兜底, 填完再调 fit_content
            self.finish(width, height)

    def _shrink_text(self):
        """文本框高度 = 实际要显示的行数(原来写死 12~17 行, 内容少时下面空一大片)。

        · wrap="none": 正文是我们自己折好塞进去的(见 App._fill_blocks), 逻辑行就是显示行;
        · wrap="char": 正文是**直接给**的那种(首启动免责弹窗、"本工具已经在运行"…), Tk 还会
          按控件宽度再折一次, 所以我们得自己按宽度估一遍 —— **估保守些**(宁可多算一行,
          也不能少算: 少了最后一行会被切掉, 那种弹窗里可能正是"怎么解决"的那句话)。
        """
        try:
            if str(self.txt.cget("wrap")) == "none":
                n = int(str(self.txt.index("end-1c")).split(".")[0])
            else:
                avail, _hang = _text_avail(self.txt, self.W, self.scale)
                f = T.font(10, scale=self.scale)
                n = 0
                for line in self.txt.get("1.0", "end-1c").split(chr(10)):
                    n += max(1, -(-f.measure(line) // avail))       # 向上取整
            self.txt.configure(height=max(6, min(int(self.txt.cget("height")), n)))
        except Exception:
            pass

    def fit_content(self):
        """正文填好之后把窗口收到内容高度(正文是构造完之后才填的, finish 那会儿还没内容)。

        原来的做法: 文本框高度写死(按构造时给的 height 折算行数), 内容少的时候下面空一大
        片 —— 免责声明/杀软白名单两个弹窗加宽后更明显。现在: 文本框高度 = 实际行数, 再按
        新的"需要高度"重设一次窗口尺寸(位置沿用第一次算好的, 不会跳)。
        """
        self._shrink_text()
        self.finish(self.W, 0, announce=False)     # height=0 → 只按内容需要的高度算


# ==================== 改快捷键的弹窗 ====================
_MOD_KEYS = {
    "Shift_L": "Shift", "Shift_R": "Shift", "Control_L": "Ctrl", "Control_R": "Ctrl",
    "Alt_L": "Alt", "Alt_R": "Alt", "Super_L": "Win", "Super_R": "Win",
}
_SYM_KEYS = {
    "grave": "`", "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "comma": ",", "period": ".",
    "slash": "/", "space": "Space", "Return": "Enter", "Tab": "Tab", "BackSpace": "Backspace",
    "Insert": "Insert", "Delete": "Delete", "Home": "Home", "End": "End",
    "Prior": "PageUp", "Next": "PageDown", "Up": "Up", "Down": "Down",
    "Left": "Left", "Right": "Right", "Caps_Lock": "CapsLock", "Pause": "Pause",
    "Scroll_Lock": "ScrollLock",
}
for _i in range(10):
    _SYM_KEYS[f"KP_{_i}"] = f"Num{_i}"
_SYM_KEYS.update({"KP_Add": "NumAdd", "KP_Subtract": "NumSub", "KP_Multiply": "NumMul",
                  "KP_Divide": "NumDiv", "KP_Decimal": "NumDec"})


def key_from_event(ev):
    """Tk 按键事件 → 配置里用的键名（'Ctrl+Alt+F5'）；识别不了返回 None"""
    ks = ev.keysym
    if ks in _MOD_KEYS or ks in ("Caps_Lock", "Num_Lock"):
        return None
    mods = []
    st = int(getattr(ev, "state", 0))
    if st & 0x0004:
        mods.append("Ctrl")
    if st & 0x20000:                   # Tk 在 Windows 上把 Alt 报成 Mod1(0x20000)。
    # 注意别把 0x8 算进来：实测那个位**永远为真**（无修饰键时也是 0x8），
    # 之前加上它导致"没按 Alt 也变成 Alt+xxx"（用户实测反馈）
        mods.append("Alt")
    if st & 0x0001:
        mods.append("Shift")
    base = None
    if ks.startswith("F") and ks[1:].isdigit() and 1 <= int(ks[1:]) <= 12:
        base = ks
    elif ks in _SYM_KEYS:
        base = _SYM_KEYS[ks]
    elif len(ks) == 1 and ks.isalnum():
        base = ks.upper()
    if not base:
        return None
    return "+".join(mods + [base])


class HotkeyDialog(DialogBase):
    """改一个快捷键：直接按新键（键盘），或手动输入组合键（鼠标侧键等只能手动输入）。

    打开期间会把其他快捷键**临时暂停**（由调用方负责），否则用户想按 F9 把它设成快捷键时，
    按下去会先把当前 F9 的功能执行一遍。
    """

    def __init__(self, app, name):
        # 460 宽刚好装下内容（输入行 ~330 + 提示文字换行 400）：再宽两边就是空白，
        # 底部两枚按钮也会被撑成 240 宽的"大而空"（用户反馈）。窄了按钮档位会自动降到
        # 160 宽（见 DialogBase.add_buttons 的按宽度选档位）。
        super().__init__(app.root, NOTICE["hk_title"] % OP_LABEL.get(name, name), app.s,
                         width=460, tag="快捷键弹窗")
        s = self.scale
        self.app, self.name, self.result = app, name, None
        b = self.body
        tk.Label(b, text=NOTICE["hk_name_label"] % OP_LABEL.get(name, name),
                 bg=T.C["bg"], fg=T.C["text"],
                 font=T.font(11, True, s), anchor="w").pack(fill="x")
        tk.Label(b, text=NOTICE["hk_cur_label"] % app.effective_key(name), bg=T.C["bg"],
                 fg=T.C["text_dim"], font=T.font(10, scale=s), anchor="w").pack(fill="x")
        self.box = tk.Label(b, text=NOTICE["hk_capture"], bg=T.C["metal_dark"],
                            fg=T.C["neon"], font=T.font(12, True, s), height=3, relief="flat",
                            highlightthickness=1, highlightbackground=T.C["metal_light"],
                            highlightcolor=T.C["neon"], takefocus=1,
                            cursor="hand2")     # 可点 ↔ 要有可点提示(项目约定 §9.3)
        self.box.pack(fill="x", pady=int(10 * s))
        self.box.bind("<Button-1>", self._focus_box)
        # 键盘可达(以前只能鼠标点, 用户实测"回车没反应"): Tab 能走到这个框, 回车/空格 =
        # 开始捕获; **已经在捕获中时**回车/空格当成"要设的键"收下 —— 所以回车/空格本身
        # 也能被设成快捷键, 并且不会一边捕获一边把弹窗的"确定"也按了(见 _box_key)。
        self.box.bind("<Return>", self._box_key)
        self.box.bind("<space>", self._box_key)
        # 焦点框自己换色(无边框窗口里 Tk 的默认焦点圈不一定画得出来, 与 ImgButton 同一套路)
        self.box.bind("<FocusIn>",
                      lambda _e: self.box.configure(highlightbackground=T.C["neon"]))
        self.box.bind("<FocusOut>", self._box_blur)
        row = tk.Frame(b, bg=T.C["bg"])
        row.pack(fill="x")
        tk.Label(row, text=NOTICE["hk_input_label"], bg=T.C["bg"], fg=T.C["text"],
                 font=T.font(10, scale=s)).pack(side="left")
        self.var = tk.StringVar(value=app.effective_key(name))
        tk.Entry(row, textvariable=self.var, bg=T.C["metal_dark"], fg=T.C["text"],
                 insertbackground=T.C["text"], font=T.font(10, scale=s), relief="flat",
                 highlightthickness=1, highlightbackground=T.C["metal_light"],
                 highlightcolor=T.C["neon"],
                 width=16).pack(side="left", padx=int(6 * s), ipady=int(2 * s))
        # "恢复默认"放这里（用户建议）：点了只把默认键位填进输入框，**不关窗也不生效**，
        # 还要点下面的"确定并生效"才写进去
        T.ImgButton(row, NOTICE["hk_default"], kind="small", command=self._use_default,
                    skin=_SKIN, scale=s, bg=T.C["bg"]).pack(side="left",
                                                           padx=(int(8 * s), 0))
        tk.Label(b, text=NOTICE["hk_tip_manual"],
                 bg=T.C["bg"], fg=T.C["text_dim"], font=T.font(9, scale=s), anchor="w",
                 justify="left", wraplength=int(400 * s)).pack(fill="x", pady=(int(8 * s), 0))
        self.lbl_err = tk.Label(b, text="", bg=T.C["bg"], fg=T.C["bad"],
                                font=T.font(10, scale=s), anchor="w", justify="left",
                                wraplength=int(400 * s))
        self.lbl_err.pack(fill="x")
        self.add_buttons([(NOTICE["hk_ok"], self._ok, True),
                          (NOTICE["cancel"], self._cancel, False)])
        self.set_close_cb(self._cancel)
        self.bind("<KeyPress>", self._on_key)
        self.bind("<Escape>", lambda _e: self._cancel())
        self._focused = False
        # 只给高度：宽度用 self.W（构造时传的 560）。以前这里写死 440 →
        # 实际窗口只有 550px, 而内容需要 ~684px, 右边连同标题栏的 ✕、
        # 按钮的右侧框线全被裁掉（用户实测"没有八叉、按钮不完整"）。
        self.finish(height=300)

    def _focus_box(self, _e=None):
        """开始捕获: 焦点落到捕获框上(焦点框变亮), 提示改成"请按下新的按键…"。

        焦点放**框自己**而不是整个弹窗: 这样 Tab 走开(比如去输入框改名字)就会自动停止
        捕获(见 _box_blur), 不会再出现"随便敲个键就把快捷键改了"。
        """
        self._focused = True
        try:
            self.box.focus_set()
        except Exception:
            pass
        self.box.configure(text=NOTICE["hk_capturing"], fg=T.C["warn"])

    def _box_key(self, ev):
        """捕获框上的回车/空格: 还没开始捕获 = 开始捕获; 已在捕获中 = 当成要设的键。"""
        if not self._focused:
            self._focus_box()
            return "break"          # 这一下只是"把焦点交给框", 不当成要设的键
        key = key_from_event(ev)
        if key:
            self.var.set(key)
            self.box.configure(text=NOTICE["hk_got"] % key, fg=T.C["good"])
        return "break"      # 别冒到弹窗的"默认按钮"上(不能一边捕获一边确定)

    def _box_blur(self, _e=None):
        """焦点离开捕获框: 停止捕获, 提示文字回到"点这里…"(别挂着"请按下新的按键")"""
        self._focused = False
        try:
            self.box.configure(highlightbackground=T.C["metal_light"])
            if self.box.cget("text") == NOTICE["hk_capturing"]:
                self.box.configure(text=NOTICE["hk_capture"], fg=T.C["neon"])
        except Exception:
            pass

    def _on_key(self, ev):
        if not self._focused:
            return
        if ev.keysym == "Escape":
            self._cancel()
            return
        key = key_from_event(ev)
        if key:
            self.var.set(key)
            self.box.configure(text=NOTICE["hk_got"] % key, fg=T.C["good"])

    def _use_default(self):
        """填入该功能的默认键位（**不关窗、不保存**；用户点"确定并生效"才写）"""
        self._keep_open = True
        try:
            d = core.DEFAULT_CONFIG["hotkeys"].get(self.name, "")
        except Exception:
            d = ""
        if d:
            self.var.set(d)
            self.lbl_err.configure(text=NOTICE["hk_default_filled"] % d, fg=T.C["neon2"])
        self._focused = False

    def _ok(self):
        key = (self.var.get() or "").strip()
        mods, vk = core.parse_key(key)
        if vk is None:
            self.lbl_err.configure(text=NOTICE["hk_bad"] % key, fg=T.C["bad"])
            self.auto_close = False          # 校验失败就别关窗
            core.beep(False, "fail")         # 按了确定但键位不合法: 出声提示
            return
        self.result = key
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()                       # 取消: 不发声（用户要求只有"确定"才响）


# ==================== 主界面 ====================




class App:
    def __init__(self):
        global _SKIN, _TK_TID
        self.state = load_state()
        self.root = tk.Tk()
        _TK_TID = threading.get_ident()      # 记住 Tk 主线程(护栏 _require_tk_thread 用)
        self.root.withdraw()
        # 自动化截图/冒烟测试用: 计时从 root 一建好就开始算, 否则弹窗阻塞时不会退出
        _test_secs = os.environ.get("SC2_UNCENSOR_UI_TEST")
        if _test_secs:
            try:
                self.root.after(int(float(_test_secs) * 1000), lambda: self.root.destroy())
            except Exception:
                pass
        self.scale = T.dpi_scale(self.root)
        self.s = self.scale
        self.skin = T.Skin(_HERE, self.scale)
        _SKIN = self.skin
        T.style_ttk(self.root, self.scale)

        self.logq = LOGQ
        self.q = queue.Queue()
        self.busy = False                 # 界面操作进行中
        self.busy_label = ""
        self._op_logpos = 0               # 本次操作开始前的日志字节数(杀软误判只看新增段)
        self.poll_busy = False
        self.poll_gap = 1000
        self.attached = False
        # 游戏连接提示音（默认开；在"设置"里勾选）
        self.beep_on_connect = bool(self.state.get("connect_beep", True))
        # 连续几次快照是"未连接"（判断是不是真的断过）。初始给个"很大"的值：
        # 界面刚起来时第一次检测到游戏，那也算"从未连接变成已连接"，要响一声
        #（用户实测："打开 UI 时游戏本来就开着，检测到就该响一下"）。
        self._disconn_streak = 99
        self.core_ready = False
        self.core_dead = False
        # 提权/重启拉起的实例带 --restart（命令行参数过得了 UAC，环境变量过不了）
        self.restarting = RESTART_FLAG in sys.argv
        self._elevating = self.restarting
        self.op_state = "idle"            # 选项操作灯: idle/manual/auto/fail/skip
        self.op_time = None               # 上一次"选项操作"发生的时刻(HH:MM), 灯上会显示
        self.hwnd = None
        self._last_notice = None
        self.edge_tail = None
        self._conn_tip = "未连接游戏。"

        # 免责声明：没同意过（或工具版本更新过）就不让进主界面
        if not self._disclaimer_ok():
            if not self._ask_disclaimer():
                self._destroy_root()
                raise SystemExit(0)

        self._build_window()
        self._seed_log()        # 先把 import 期的横幅补进面板, 再起核心线程(顺序才不乱)
        if self.restarting:
            # 提权/重启拉起的实例：先在后台等上一个实例真的退出，再启动核心
            # （抢跑会撞单实例锁，而且核心判过一次"已存在"后本进程内不会再成功）
            threading.Thread(target=lambda: (self._wait_other_instances(),
                                             self._start_core()), daemon=True).start()
        else:
            self._start_core()
        if not self.state.get("antivirus_seen"):
            self.state["antivirus_seen"] = True
            save_state(self.state)
            self.after(300, self.show_antivirus)
        self.root.after(120, self._pump)
        self.root.after(400, self._poll)
        self._test_hooks()

    def _when_core_ready(self, fn, delay_ms=1000, limit_ms=2500):
        """自动化用例用: 等**核心就绪**(或超时)再执行 fn。

        为什么不能像别处那样写死延时: 核心就绪时间随机器负载/杀软扫描会变（0.3s~几秒），
        固定 1.5s 在慢机器上会抢跑 —— 表现为用例偶发失败、提示区停在"快捷键线程还没起来"
        （2026-09-15 实测到一次）。这里轮询 core_ready, 到 limit_ms 还没就绪也照常执行,
        免得用例永远不结束。
        """
        def tick(t0):
            if (self.core_ready or self.core_dead
                    or (time.time() - t0) * 1000 >= limit_ms):
                fn()
                return
            self.root.after(100, lambda: tick(t0))
        self.root.after(delay_ms, lambda: tick(time.time()))

    def _test_hooks(self):
        """自动化用例入口（环境变量触发，正常使用不会走到）"""
        open_hk = os.environ.get("SC2_UNCENSOR_UI_OPEN_HOTKEY")
        if open_hk:
            self.root.after(900, lambda: self.edit_hotkey(open_hk))
        set_hk = os.environ.get("SC2_UNCENSOR_UI_SET_HOTKEY")
        if set_hk and "=" in set_hk:
            n, k = set_hk.split("=", 1)
            self.root.after(900, lambda: self._save_hotkey(n, k))
        act = os.environ.get("SC2_UNCENSOR_UI_TEST_ACT")

        def report_hint(marker="内存=", wait_ms=2500):
            """打印提示区内容: 轮询到它**真的出现状态**(默认找 "内存=")为止, 或超时。

            ⚠️ 不能用"派发后固定等 N 毫秒": 进程被杀软扫描/机器负载拖住时, 核心处理一条
            WM_HOTKEY 可能晚 2~3 秒(2026-09-15 实测: 就绪 42.9s → [按键] status 45.6s),
            固定等待会让用例偶发失败。轮询到状态出现/超时, 断言的才是"功能对不对",
            而不是"这台机器够不够快"。超时必须留在用例进程存活窗口(RUN_SECS=7)内。
            """
            t0 = time.time()

            def tick():
                txt = self.hint_lbl.cget("text")
                if marker in txt or (time.time() - t0) * 1000 >= wait_ms:
                    print("[界面] 提示区: %s" % txt)
                    return
                self.root.after(100, tick)

            self.root.after(200, tick)

        if act == "minimize":
            self.root.after(1500, self._test_minimize)
        elif act == "status":                # "显示当前状态"按钮的流程用例
            # 提示区停在"快捷键线程还没起来"= act 抢在核心就绪之前跑了(见 _when_core_ready),
            # 所以先等就绪再点按钮, 结果稳定可断言
            self._when_core_ready(lambda: (self.act("status"), report_hint()))
        elif act == "hotkey":                # 模拟按一次快捷键(不碰用户键盘: 直接投 WM_HOTKEY)
            self._when_core_ready(lambda: (self._test_press_hotkey(), report_hint()))
        elif act == "master":                # 总开关按钮状态用例(见 smoke 的 paused 模式)
            def report_master():
                b = getattr(self, "btn_hotkey_master", None) or getattr(self, "btn_master", None)
                if b is None:
                    print("[界面] 总开关按钮: 不存在")
                    return
                print("[界面] 总开关按钮: key=%r lamp=%r" % (b._key, b._lamp))
            self.root.after(2500, report_master)
        elif act == "quicktoggle":           # 「快速上手」收起/展开后页签条**不许移位**
            # 2026-09-16 修的那个 bug: 初排与重排用了两套取整(54 vs 54.4), 首次点开/收起后
            # 下面整块往上跳 10px。把两次收起后的页签起点打出来, 用例断言两次数值相同。
            def report_layout(tag):
                print("[界面] 布局@%s 页签起点y=%d 快速上手块y=%d 逻辑高=%s 页面区y=%s"
                      % (tag, int(self.tab_y), int(self.quick_y),
                         getattr(self, "_quick_h", None), self.page_host.place_info().get("y")))
            self.root.after(700, lambda: report_layout("初始"))
            self.root.after(1000, self._toggle_quickstart)
            self.root.after(1500, lambda: report_layout("展开后"))
            self.root.after(1800, self._toggle_quickstart)
            self.root.after(2300, lambda: report_layout("收起后"))
            self.root.after(2600, self._toggle_quickstart)      # 再来一轮(查累积漂移)
            self.root.after(3100, self._toggle_quickstart)
            self.root.after(3600, lambda: report_layout("再收起"))
        elif act == "terms":                 # 弹窗按钮有效性用例
            self.root.after(1200, self.show_terms)
            self.root.after(3200, self._test_notice_button)
        elif act in ("help", "av", "disc"):
            # 三个文字弹窗的**排版自检**(截图 + 断言): 先开出来, 再逐行核对折行结果
            #   · 行首标点行数 = 0 —— 中文禁则(标点不落行首)
            #   · 尾行短行数 = 0 —— "只有一两个字跑到下一行"是用户反复提过的毛病
            opener = {"help": self.show_help, "av": self.show_antivirus,
                      "disc": self.show_disclaimer}[act]
            label = {"help": "使用说明", "av": "杀软白名单", "disc": "免责声明"}[act]
            self.root.after(1200, opener)

            def check_doc():
                dlg = self._last_notice
                if dlg is None or not dlg.winfo_exists():
                    print("[界面] %s: 弹窗不存在" % label)
                    return
                lines = [l for l in dlg.txt.get("1.0", "end-1c").split(chr(10)) if l.strip()]
                # 中文禁则自检: 折行后不该有"行首是标点"的行(自己折行时按禁则选断点)
                kin = [l for l in lines if l.lstrip()[:1] in "、。，；：？！）】》」』"]
                short = [l.strip() for l in lines if len(l.strip()) <= 3]
                print("[界面] %s: 行首标点行数=%d" % (label, len(kin)))
                # 重新折行后, 每个"段落"应当以句号/右括号等收尾; 若某行以逗号、顿号结尾,
                # 说明它还是被 py 里的硬换行切断了(排版又坏了)
                bad = [l for l in lines if l.rstrip()[-1] in "，、；,;"]
                print("[界面] %s: 段落行数=%d 以逗号结尾的行=%d 尾行短行数=%d"
                      % (label, len(lines), len(bad), len(short)))
                self._test_notice_button()
            self.root.after(3200, check_doc)

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def _destroy_root(self):
        try:
            self.root.destroy()
        except Exception:
            pass          # 测试计时器可能已经先销毁过 root(重复 destroy 会抛 TclError)

    # ---------- 免责声明 ----------
    def _disclaimer_ok(self):
        d = self.state.get("disclaimer") or {}
        return bool(d.get("accepted")) and d.get("version") == core.VERSION

    def _ask_disclaimer(self):
        agreed = {"v": False}
        dlg = Notice(None, NOTICE["disclaimer_title"], DISCLAIMER,
                     [(NOTICE["agree"], lambda: None, True),
                      (NOTICE["quit"], lambda: None, False)],
                     scale=self.s, width=560, height=430, auto_close=False, tag="免责弹窗",
                     check=(NOTICE["agree_check"], False, None))

        def do_agree():
            if not dlg.check_var.get():
                return                        # 没勾就不关窗(不可跳过)
            agreed["v"] = True
            self.state["disclaimer"] = {"accepted": True, "version": core.VERSION,
                                        "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_state(self.state)
            dlg.destroy()

        def do_quit():
            agreed["v"] = False
            dlg.destroy()

        dlg._btns[0].command = do_agree
        dlg._btns[1].command = do_quit
        dlg.set_close_cb(do_quit)
        dlg.set_enabled(0, False)
        dlg.chk.configure(command=lambda: dlg.set_enabled(0, dlg.check_var.get()))
        dlg.wait_window()
        return agreed["v"]

    # ---------- 窗口骨架 ----------
    def _build_window(self):
        s, r = self.s, self.root
        W, H = int(T.WINDOW_W * s), int(T.WINDOW_H * s)
        x, y = self._initial_pos(W, H)
        r.title(APP_TITLE)          # 任务栏按钮/悬浮提示用得到
        # 尺寸交给 Tk, **位置交给 Win32**(_frameless 里的 SetWindowPos):
        # Tk 的 geometry 偏移与 Win32 窗口坐标在缩放显示下不是同一套单位, 混用会漂移
        r.geometry(f"{W}x{H}+0+0")
        r.configure(bg=T.C["bg"])
        try:
            r.update_idletasks()
            self._frameless(W, H, x, y)     # 去掉原生标题栏(保留系统最小化与任务栏按钮)
            r.lift()
            r.focus_force()
            if os.environ.get("SC2_UNCENSOR_UI_TEST"):     # 自动化截图要确保在最前面
                r.attributes("-topmost", True)
        except Exception as e:
            print(f"[界面] 无边框设置失败(退回带原生标题栏): {e}")

        self.canvas = tk.Canvas(r, width=W, height=H, bg=T.C["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        frame = self.skin.img("window_frame", (T.WINDOW_W, T.WINDOW_H))
        if frame:
            self.canvas.create_image(0, 0, anchor="nw", image=frame)
        else:
            self.canvas.create_rectangle(0, 0, W - 1, H - 1, outline=T.C["metal_light"],
                                         fill=T.C["bg"])
        # ---------- 标题栏（位置全部由几何常量推导，别写死魔法数） ----------
        pad = 14                                    # 内容区左右留白
        x0 = T.CONTENT_X0                            # 内容区左边界
        cw = T.CONTENT_W                             # 内容区宽度
        title_f = T.font(13, True, s)
        head_y = T.FRAME_INSET[1] // 2 + 1           # 顶栏竖直中线附近
        self.canvas.create_text(int(x0 * s), int(head_y * s), anchor="w", text=APP_TITLE,
                                fill=T.C["text"], font=title_f)
        # 版本号紧跟标题（用字体实际宽度算，标题改长了也不会撞）。
        # ⚠️ title_f.measure() 返回的是**像素**（字号是负数像素制），而本窗口的坐标全是
        # "逻辑值 × s"，所以这里必须把量出来的像素宽**除以 s 换回逻辑单位**；以前直接拿它
        # 当逻辑值又乘了一次 s，缩放 125% 下间距被放大成 ~90px —— 用户实测反馈
        # "版本号跟左边的标题有点远了"，根因就是这个双重缩放。
        ver_f = T.font(9, scale=s)
        ver_x = x0 + int(round(title_f.measure(APP_TITLE) / s)) + 10
        self.canvas.create_text(int(ver_x * s), int((head_y + 2) * s), anchor="w",
                                text=f"v{core.VERSION}", fill=T.C["text_dim"], font=ver_f)
        # 标题栏按钮：只留 最小化 / 关闭（中间那枚"置顶"没什么用，已去掉）
        WIN_BTN, WIN_GAP = 18, 4
        btn_y = (T.FRAME_INSET[1] - WIN_BTN) // 2
        right_edge = T.WINDOW_W - T.FRAME_INSET[2] - pad
        win_specs = (("min", TOOLBAR["min"]), ("close", TOOLBAR["close"]))
        self.winbtns = {}
        for i, (key, tip) in enumerate(reversed(win_specs)):     # 从右往左摆
            bx = right_edge - WIN_BTN - i * (WIN_BTN + WIN_GAP)
            self.winbtns[key] = self._add_win_btn(key, bx, btn_y, tip)
        # 作者：标题栏右侧、按钮左边（惯例：标题栏=程序名/版本/作者）。
        # 名字用简称（地方窄，长署名会撞到版本号/按钮）；鼠标悬停显示完整署名。
        # 字号自适应：全名较长时自动缩一档，保证"标题—版本—作者—按钮"四段不重叠。
        author_x = right_edge - len(win_specs) * (WIN_BTN + WIN_GAP) - 8
        ver_end = ver_x + int(round(ver_f.measure(f"v{core.VERSION}") / s))
        author_txt = TX.FOOTER_AUTHOR % _author_brief()
        author_f = ver_f
        for _base in (9, 8, 7):
            cand = T.font(_base, scale=s)
            if ver_end + 12 + int(round(cand.measure(author_txt) / s)) <= author_x:
                author_f = cand
                break
        self.canvas.create_text(int(author_x * s), int((head_y + 2) * s), anchor="e",
                                text=author_txt, fill=T.C["text_dim"], font=author_f,
                                tags=("author",))
        self.canvas.tag_bind("author", "<Enter>",
                             lambda _e: self._tip(TX.AUTHOR_TIP_FMT % _author_full()))
        self.canvas.tag_bind("author", "<Leave>", lambda _e: self._tip(None))
        self._bind_drag()
        r.bind("<Escape>", lambda _e: self._on_esc())
        r.bind("<F1>", lambda _e: self.show_help())

        # ---------- 竖排：用 _Stack 一行行往下排 ----------
        # 以前这里是一串写死的 y（52/88/120/134/198/252/292…），改窗口高度或某个组件
        # 高度就得手改一圈；现在只描述"行的顺序 + 每行高度"，y 自动算出来，
        # 辅助线和折叠区都跟着上一行的高度走。
        st = _Stack(T.CONTENT_Y0, gap=10)

        def sep_line(gap=8):
            """在当前位置画一条横向分隔线，并把游标下移（返回线对象，便于事后挪动）"""
            ly = st.sep(gap)
            return self.canvas.create_line(int(x0 * s), int(ly * s),
                                           int((x0 + cw) * s), int(ly * s),
                                           fill=T.C["metal_light"])

        # ---- 状态区 ----
        # 连接状态就一行（原因并入这一行的文字，完整原因在悬停提示里）
        conn_y = st.row(18)
        self.lamp_conn = T.Lamp(r, "正在连接…", self.skin, s, "off",
                                tooltip=lambda: self._conn_tip)
        self.lamp_conn.place(x=int(x0 * s), y=int(conn_y * s))
        # 这一行右边再挂一句"初次使用…"的引导（用户要求：书面一点、醒目一点）。
        # 画法: 先量文字宽度 → 垫一个描边底片 → 再画字；悬停给详细说明, 点它打开"使用说明"。
        hint_f = T.font(8, True, s)
        _hh_txt = CONN_HINT
        _hw = int(round(hint_f.measure(_hh_txt) / s))          # 像素宽 → 逻辑单位
        _hx1 = x0 + cw
        self.canvas.create_rectangle(int((_hx1 - _hw - 10) * s), int((conn_y - 1) * s),
                                     int(_hx1 * s), int((conn_y + 17) * s),
                                     fill=T.C["metal_dark"], outline=T.C["warn"],
                                     tags=("connhintbg",))
        self._hint_item = self.canvas.create_text(int((_hx1 - 5) * s), int((conn_y + 8) * s),
                                                  anchor="e", text=_hh_txt, fill=T.C["warn"],
                                                  font=hint_f, tags=("connhint",))
        self.canvas.tag_bind("connhint", "<Button-1>", lambda _e: self.show_help())
        self.canvas.tag_bind("connhint", "<Enter>", lambda _e: self._hover_conn_hint(True))
        self.canvas.tag_bind("connhint", "<Leave>", lambda _e: self._hover_conn_hint(False))

        # 三个状态灯：内存操作 / 选项状态(游戏里的值) / 选项操作(那一下点击做过没有)
        self.lamps = {}
        lamp_y = st.row(18)
        for i, key in enumerate(("mem", "opt", "act")):
            lamp = T.Lamp(r, "", self.skin, s, "off", bg=T.C["bg"])
            lamp.place(x=int((x0 + i * cw // 3) * s), y=int(lamp_y * s))   # 三盏均匀铺开
            self.lamps[key] = lamp
        T.ToolTip(self.lamps["mem"].label, lambda: LAMP_TIPS["mem"], scale=s)
        T.ToolTip(self.lamps["opt"].label, lambda: LAMP_TIPS["opt"], scale=s)
        T.ToolTip(self.lamps["act"].label, lambda: OP_STATE[self.op_state][2], scale=s)
        sep_line()

        # ---- 主操作区：手动模式反和谐 / 自动模式反和谐（并排居中），下面总开关（居中）----
        hero_w, hero_h = T.BTN_SIZES["hero"]
        big_w, big_h = T.BTN_SIZES["big"]
        pair = hero_w * 2 + 20
        pair_x = x0 + (cw - pair) // 2
        row_y = st.row(hero_h, gap=12)
        self.btn_uncensor = self._btn(r, "uncensor", kind="hero")
        self.btn_uncensor.place(x=int(pair_x * s), y=int(row_y * s))
        self.btn_main = self._btn(r, "uncensor_auto", kind="hero")
        self.btn_main.place(x=int((pair_x + hero_w + 20) * s), y=int(row_y * s))
        self.btn_master = T.ImgButton(
            r, OP_LABEL["hotkey_master"], self.effective_key("hotkey_master"), kind="big",
            command=self._toggle_master, skin=self.skin, scale=s, bg=T.C["bg"],
            tooltip=lambda: _cfg_tip("hotkeys.hotkey_master"))
        # ⚠️ 必须按 `btn_<名字>` 约定再挂一份: refresh_hotkeys() 是按 OP_LABEL 里的名字
        # getattr(self, "btn_" + name) 逐个更新的, 而这里原本只叫 btn_master —— 于是
        # 总开关那条分支永远取不到按钮、直接 continue, "启用中/已暂停"从来没显示出来过
        # (2026-09-15 修标题栏时顺带发现: 截图里暂停态的总开关还是光秃秃一个键位)。
        self.btn_hotkey_master = self.btn_master
        self.btn_master.place(x=int((x0 + (cw - big_w) // 2) * s), y=int(st.row(big_h) * s))

        # ---- 提示区 ----
        hint_h = 34
        self.hint = tk.Frame(r, bg=T.C["panel"], highlightthickness=1,
                             highlightbackground=T.C["metal_light"])
        self.hint.place(x=int(x0 * s), y=int(st.row(hint_h) * s),
                        width=int(cw * s), height=int(hint_h * s))
        self.hint_lamp = T.Lamp(self.hint, "", self.skin, s, "info", bg=T.C["panel"])
        self.hint_lamp.pack(side="left", padx=(int(6 * s), 0))
        self.hint_lbl = tk.Label(self.hint, text=HINT["ready"],
                                 bg=T.C["panel"], fg=T.C["text_dim"], font=T.font(9, scale=s),
                                 anchor="w", justify="left")
        self.hint_lbl.pack(side="left", padx=(int(4 * s), int(6 * s)), fill="x", expand=True)
        sep_line()

        # ---- 快速上手（可展开的常驻说明块，用户要求）----
        self.quick_panel = self._build_quickstart(r, x0, cw, st)
        self.quick_sep = sep_line()

        # ---- 页签区（吃掉剩余高度）：四个折叠区改成横向页签，点哪个显示哪页 ----
        self.tab_y = st.row(24)
        self._build_tabs(r, x0, cw, self.tab_y)
        # 页面容器：从页签下方一直铺到底部按钮行上方（日志页的文本框会跟着变高）
        # 页面**紧贴**页签条下沿：选中页签不封底边、底部窄渐变到页面底色, 两者连成一体
        # 页面区的 y 用**一次取整** int((tab_y+TAB_H)*s)，与页签画布的底边同一套算法
        # （写成"两次取整相加"会差 1px，把页面区顶到渐变终点那一行上 —— 见 _layout_tabs_and_pages）
        host_y = self.tab_y + self.TAB_H          # 紧贴页签条的分界线(页签与内容连成一体)
        self.bottom_h = T.BTN_SIZES["wide"][1]
        self.page_gap = 18              # 页面区与底部按钮行之间的留白(原来 10, 展开时太挤)
        self.bottom_y = T.WINDOW_H - T.FRAME_INSET[3] - self.bottom_h - 6   # 再往上 6px 透气
        self.page_host = tk.Frame(r, bg=T.C["bg"])
        self.page_host.place(x=int(x0 * s), y=int(host_y * s),
                             width=int(cw * s),
                             height=int((self.bottom_y - self.page_gap - host_y) * s))
        self.pages = {}
        self.page_inset = 5         # 页面内容左侧内缩(逻辑): 给覆盖层那条收束线让出列
        for key, _title in TABS:
            p = tk.Frame(self.page_host, bg=T.C["bg"])
            self.pages[key] = p
        self._fill_more(self.pages["more"])
        self._fill_hotkeys(self.pages["hotkeys"])
        self._fill_settings(self.pages["settings"])
        self._fill_log(self.pages["log"])
        self._select_tab(self.state.get("tab") or "more", save=False, initial=True)
        # ---- 底部常驻的三个说明按钮（固定贴底）----
        self._build_bottom_bar(r, x0, cw, self.bottom_y)
        # 页签条抬到页面容器之上: 页面容器是后建的, 页签自己的绘制别被它压住
        self.tab_bar.lift()
        # 左沿收束的**覆盖层**(用户建议: 用能自由重叠的东西盖上去, 页面内容就不用下移了)。
        # 建在页面容器之后 → Tk 里后建的兄弟控件在上面, 于是它天然盖在页面内容之上;
        # 底色与页面一致, 所以除了那条收束线本身看不出来。
        self.edge_tail = tk.Canvas(r, bg=T.C["bg"], highlightthickness=0, bd=0,
                                   width=1, height=1)
        self.edge_tail.place_forget()
        # ⚠️ 首次选中页签发生在**这块画布建好之前**(顺序: 建页签 → 建页面 → 选中),
        #    所以要在这里补一次, 否则一开始看不到那条收束
        if (self.state.get("tab") or "more") == TABS[0][0]:
            self._edge_tail(self.tab_line_w)
        self._taskbar()
        self.root.after(150, self._bring_to_front)
        self._render_op_lamp()
        self.refresh_hotkeys()      # 先把"启用中"这个初始状态(小灯/配色)画上, 别等核心就绪
        # 页签/页面起始 y 与底部按钮行的 y 都打出来: 自动化(截图/布局回归)要按它判断有没有重叠
        print(f"[界面] 窗口 {W}x{H}+{x}+{y} 缩放={s:.2f} 数据目录={DATA_DIR}")
        print(f"[界面] 布局: 页签起点y={int(self.tab_y)} 页面高度={int(self.bottom_y - 10 - host_y)} "
              f"底部按钮行y={int(self.bottom_y)} "
              f"快速上手={'展开' if self.state.get('quick') else '收起'}"
              f"(块高{int(self.quick_panel.winfo_reqheight() / s)})")
        # 布局重排的**同一个口径**也打一行: 展开/收起「快速上手」时下面整块会按这套数重算,
        # 两套取整要是对不上, 页签条会"跳一下"(2026-09-16 修: 初排 54 与重排 54.4 差 10px)。
        # 自动化用例就把这行和上面那行对比着看。
        print(f"[界面] 布局细节: 快速上手块y={int(self.quick_y)} 逻辑高={int(self._quick_h)} "
              f"分隔线y={self.canvas.coords(self.quick_sep)[1]:.0f} "
              f"页面区y={int(self.page_host.place_info()['y'])}")

    def _build_quickstart(self, root, x0, cw, st):
        """「快速上手」块（常驻、可展开，用户要求）。

        · 收起：只显示一行**白字**说明（快捷上手那句），"主界面/请勿同时操作"用醒目色标出；
          右侧有 ▸ + "展开详细说明" 提示这是可点的。
        · 展开：在这一行下面列出 QUICKSTART 的详细条目（收起那行**不消失**，仍在最上面）。
        点标题行任意位置切换展开/收起，展开状态存 ui_state.json（"quick" 键）。
        """
        s = self.s
        panel = tk.Frame(root, bg=T.C["panel"], highlightthickness=1,
                         highlightbackground=T.C["metal_light"])
        # 标题行: ▸/▾ + 标题 + 右侧提示（整行可点）
        # 标题行做成一条"可点的条": 底色调亮一档 + 1px 描边 + 大一点的三角 ——
        # 光靠一个小三角, 用户看不出这是能点的(实测反馈)。
        bar = tk.Frame(panel, bg=T.C["metal"], cursor="hand2",
                       highlightthickness=1, highlightbackground=T.C["metal_light"],
                       highlightcolor=T.C["neon"], takefocus=1)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=T.C["metal"], cursor="hand2")
        inner.pack(fill="x", padx=int(9 * s), pady=int(1 * s))
        # 三角画在小画布上, 不用文字字形: 22 号字形的行盒有 45px 高, 会把标题条整个撑起来
        # (实测块高 74 里有 48 是标题条); 画出来则尺寸精确、盒子紧凑, 还能画得更大更清晰。
        self.qs_arrow = tk.Canvas(inner, width=int(18 * s), height=int(18 * s),
                                  bg=T.C["metal"], highlightthickness=0, bd=0,
                                  cursor="hand2")
        self.qs_arrow.pack(side="left")
        _qs_title = tk.Label(inner, text=QUICKSTART_TITLE, bg=T.C["metal"], fg=T.C["text"],
                             font=T.font(10, True, s), cursor="hand2")
        _qs_title.pack(side="left", padx=(int(5 * s), 0))
        self.qs_hint = tk.Label(inner, text=QUICKSTART_EXPAND, bg=T.C["metal"],
                                fg=T.C["neon2"], font=T.font(8, scale=s), cursor="hand2")
        self.qs_hint.pack(side="right")
        # 整条都能点(含内层)
        for w in (bar, inner, self.qs_arrow, _qs_title, self.qs_hint):
            w.bind("<Button-1>", lambda _e: self._toggle_quickstart())
        # ⚠️ 键盘绑定必须挂在**拿得到焦点的那一层**(bar, 它才有 takefocus=1):
        #    原来 takefocus 在 bar 上、回车/空格却绑在 inner 上, 于是 Tab 走到标题条后
        #    按回车/空格一点反应都没有(用户实测反馈"好多地方按回车没反应", 根因就在这)。
        bar.bind("<Return>", self._qs_key)
        bar.bind("<space>", self._qs_key)
        bar.bind("<FocusIn>", lambda _e: self._qs_focus(True))
        bar.bind("<FocusOut>", lambda _e: self._qs_focus(False))
        self.qs_head_bar = bar
        # 收起时也常驻的那一行（白字；高亮片段用橙色）——用 Text 才能在一行里混两种颜色
        self.qs_head = tk.Text(panel, height=2, wrap="word", bg=T.C["panel"],
                               fg=T.C["text"], relief="flat", bd=0, highlightthickness=0,
                               font=T.font(9, scale=s), cursor="hand2",
                               padx=0, pady=0, insertwidth=0, spacing1=0)
        # 收起行的上下留白取**均等**(4/4): 用户实测"离上框线比下面窄一点", 居中即可;
        # 总留白与原来(3+5)相同, 所以行高不变
        self.qs_head.pack(fill="x", padx=int(10 * s), pady=(int(4 * s), int(4 * s)))
        self.qs_head.tag_configure("hl", foreground=T.C["warn"])
        self.qs_head.bind("<Button-1>", lambda _e: self._toggle_quickstart())
        # 展开后才显示的详细条目
        self.qs_body = tk.Frame(panel, bg=T.C["panel"])
        for txt, emph in QUICKSTART:
            tk.Label(self.qs_body, text=txt, bg=T.C["panel"],
                     fg=T.C["warn_soft"] if emph else T.C["text_soft"],
                     font=T.font(9, scale=s), anchor="w", justify="left",
                     wraplength=int((cw - 30) * s)).pack(fill="x", padx=int(10 * s))
        self._render_quickstart()
        # 高度按实际内容量排（收起/展开不同高度；_Stack 用它决定后面的 y）。
        # ⚠️ 布局用的逻辑高度必须**取整**成整数记下来（_quick_h），_layout_tabs_and_pages
        #    重算时用同一个数：以前初排用 int(reqh/s)=54（浮点 54.4 取整）、展开/收起时却用
        #    原始 54.4，首次展开/收起后下面整块会往上跳 10px。
        #    面板自己的 place 高度则用**实测物理像素**（不乘不除），展开时才长得对。
        panel.update_idletasks()
        h = int(panel.winfo_reqheight() / s)
        y = st.row(h)
        panel.place(x=int(x0 * s), y=int(y * s), width=int(cw * s),
                    height=panel.winfo_reqheight())
        self.quick_panel, self.quick_y = panel, y
        self._quick_h = h            # 逻辑高度(取整) —— 布局重算的唯一来源
        return panel

    def _qs_key(self, _e=None):
        """标题条上的回车/空格 = 展开/收起(与鼠标点一下等价)"""
        self._toggle_quickstart()
        return "break"

    def _qs_focus(self, on):
        """焦点框自己换色: 无边框窗口里 Tk 默认的焦点圈看不出来, 键盘用户不知道焦点在哪"""
        try:
            self.qs_head_bar.configure(highlightbackground=T.C["neon"] if on
                                       else T.C["metal_light"])
        except Exception:
            pass

    def _render_quickstart(self):
        """把"收起时那行"和"展开后的条目"按当前状态画出来。

        收起行里的 {} 换成**当前**自动模式反和谐的快捷键（用户改键后这里跟着变）。
        """
        expanded = bool(self.state.get("quick"))
        s = self.s
        key = self.effective_key("uncensor_auto") or "F9"
        text = QUICKSTART_HEAD.format(key)
        t = self.qs_head
        t.configure(state="normal")
        t.delete("1.0", "end")
        # 按 QUICKSTART_HL 里的片段切分, 命中的片段标成橙色
        pos, hl = 0, []
        while pos < len(text):
            nxt = min([(text.find(w, pos), w) for w in QUICKSTART_HL if text.find(w, pos) >= 0]
                      or [(len(text), "")])
            if nxt[0] < 0:
                break
            t.insert("end", text[pos:nxt[0]])
            t.insert("end", nxt[1], "hl")
            pos = nxt[0] + len(nxt[1])
        t.insert("end", text[pos:])
        # 高度按**实际折行数**定: 用户把这句话改短了以后一行就放得下, 还按 2 行占高会白留一片
        try:
            t.update_idletasks()
            _lines = int(str(t.count("1.0", "end-1c", "displaylines")).strip("()").split()[0])
        except Exception:
            _lines = 0
        t.configure(height=max(1, min(2, _lines)) if _lines else 1)
        t.configure(state="disabled")
        # 三角: 展开=下三角(▾) 收起=右三角(▸); 在 20×20 的画布里画, 尺寸好控
        a = self.qs_arrow
        a.delete("all")
        # 尺寸跟文字差不多大(用户要求: 太大反而丑): 画布里约 13×13, 与"快速上手"字高相当
        if expanded:
            self.qs_body.pack(fill="x", pady=(int(3 * s), int(3 * s)))
            a.create_polygon(int(3 * s), int(6 * s), int(15 * s), int(6 * s),
                             int(9 * s), int(15 * s), fill=T.C["neon"], outline="")
            self.qs_hint.configure(text=QUICKSTART_COLLAPSE)
        else:
            self.qs_body.pack_forget()
            a.create_polygon(int(5 * s), int(3 * s), int(14 * s), int(9 * s),
                             int(5 * s), int(15 * s), fill=T.C["neon"], outline="")
            self.qs_hint.configure(text=QUICKSTART_EXPAND)

    def _toggle_quickstart(self):
        """展开/收起「快速上手」: 只改这块自己的高度, 页面区(日志框)跟着伸缩。"""
        self.state["quick"] = not bool(self.state.get("quick"))
        save_state(self.state)
        self._render_quickstart()
        self.quick_panel.update_idletasks()
        px = self.quick_panel.winfo_reqheight()      # 面板自身的物理高度(实测)
        # ⚠️ 这里必须 place_configure(height=...): 面板当初是用 place(height=...) 摆的,
        #    显式高度会盖住控件的请求高度 —— 只 configure(height) 的话块不会长大,
        #    下面的页签却被推下去了(用户实测: "下面动了, 快速上手还是没展开")。
        self.quick_panel.place_configure(height=px)
        # 布局用的逻辑高度与初排**同一个取整口径**（见 _build_quickstart）：
        # 面板物理高度 ÷ 缩放 再取整 —— 直接拿浮点去算会让下面的块跳 10px
        self._quick_h = int(px / self.s)
        # 这块高度变了 → 下面页签/页面区的位置也要跟着走（不然会重叠或被留白）
        self._layout_tabs_and_pages()

    def _layout_tabs_and_pages(self):
        """按「快速上手」块当前高度, 重新摆分隔线/页签/页面区（展开/收起时调用）。

        页面区高度是"页签下方 → 底部按钮行上方"的全部剩余空间, 所以这块一展开/收起,
        下面的日志框就跟着变矮/变高（用户要的就是这个效果）。
        """
        s = self.s
        # 用"建块时记下的逻辑高度"（不是现场 winfo_reqheight()/s）—— 见 _build_quickstart 的
        # ⚠️：没取整的那份让初排与重排差 10px，首次展开/收起时整块会跳一下。
        h = getattr(self, "_quick_h", None)
        if h is None:                                  # 兜底(理论上到不了)
            h = int(self.quick_panel.winfo_reqheight() / s)
        # +18 = 分隔线前的 8 + 分隔线后的 10（_Stack 的 gap）：与初排完全同一套数
        self.tab_y = int(self.quick_y + h + 18)
        # 分隔线的 y 用与初排**逐字相同**的算式：初排那条线是 `_Stack.sep(8)` 在
        # "游标=块底(小数)+8"处画的，所以这里是 (quick_y + h + 8) * 缩放 —— 只取整一次，
        # 与面板的取整不叠加（早先这里直接拿 tab_y 当线位，比初排低 10px：展开/收起一次后
        # 块与线之间的留白会从 12px 变成 22px，跳一下）
        sep_y = (self.quick_y + h + 8) * s
        try:
            self.canvas.coords(self.quick_sep,
                               int(T.CONTENT_X0 * s), sep_y,
                               int((T.CONTENT_X0 + T.CONTENT_W) * s), sep_y)
            self.tab_bar.place_configure(y=int(self.tab_y * s))
            if (self.state.get("tab") or "more") == TABS[0][0]:
                self._edge_tail(self.tab_line_w)
                self._left_ring(getattr(self, "_tab_focus_key", None) == TABS[0][0])
        except Exception:
            pass
        host_y = self.tab_y + self.TAB_H
        try:
            # ⚠️ y 用**一次取整** int((tab_y+TAB_H)*s)：与初排、与页签画布底边都是同一套算法。
            #    写成 int(tab_y*s)+int(TAB_H*s) 在 125% 下会少 1px（把页面区顶到页签底色渐变
            #    的最后一行上，让"渐变终点落在分割线下面那一行"这件设计落空）。
            self.page_host.place_configure(y=int(host_y * s),
                                           height=int((self.bottom_y - self.page_gap - host_y) * s))
        except Exception:
            pass

    def _build_tabs(self, root, x0, cw, y):
        """横向页签行（取代原来四个竖向折叠区）。点哪个显示哪页。

        **每个页签一块自己的画布**（都 takefocus=1）：Tab 能一个个走过所有页签、回车/空格
        切到那一页。以前整条只有一块画布、只有一个键盘焦点 —— Tab 停在"当前选中的那个页签"
        上，别的页签到不了也切不了页（用户实测反馈）。
        画布首尾**相接不留缝**，分割线才不会在接缝处断成虚线（尾巴那块画布放右侧提示）。
        """
        s = self.s
        # 画布比页签条多露 TAB_EXTRA 物理像素: 底色要收到分割线**下面那一行**,
        # 最左页签那根竖线还要在框线以下再收束一小段
        ch = int(self.TAB_H * s) + self.TAB_EXTRA
        self.tab_bar = tk.Frame(root, bg=T.C["bg"])
        cwp = int(cw * s)
        self.tab_bar.place(x=int(x0 * s), y=int(y * s), width=cwp, height=ch)
        boxes, spans = self._tab_layout()
        # 边界换成物理像素后**相邻对齐**（右边界取下一块的左边界）：place 各自取整会差出
        # 一两个像素，接缝处就会露出底色、分割线看着是断的
        xs = [int(round(sp[1] * s)) for sp in spans] + [int(round(spans[-1][2] * s))]
        self.tab_widgets, self.tab_geo = {}, {}
        for i, (key, bx0, bx1) in enumerate(boxes):
            cx0, cx1 = xs[i], xs[i + 1]
            c = tk.Canvas(self.tab_bar, width=max(1, cx1 - cx0), height=ch,
                          bg=T.C["bg"], highlightthickness=0, bd=0, takefocus=1,
                          cursor="hand2")
            c.place(x=cx0, y=0)
            self.tab_widgets[key] = c
            # 画布的横向范围(逻辑) + **实际像素宽**: 画布比页签框左右各宽半个间距, 分割线画满
            # 整块画布; 像素宽必须取实际值 —— 用逻辑宽乘缩放再取整会差 1px, 接缝处就漏一格线
            self.tab_geo[key] = (cx0 / s, cx1 / s, cx1 - cx0)
            c.bind("<Button-1>", lambda e, k=key: self._on_tab_click(e, k))
            c.bind("<Motion>", lambda e, k=key: self._on_tab_hover(e, k))
            c.bind("<Leave>", lambda _e: self._on_tab_hover_clear())
            c.bind("<FocusIn>", lambda _e, k=key: self._tab_focus(k, True))
            c.bind("<FocusOut>", lambda _e, k=key: self._tab_focus(k, False))
            c.bind("<Return>", lambda _e: self._tab_activate())
            c.bind("<space>", lambda _e: self._tab_activate())
            # 方向键把"按键来自哪个页签"直接传下去当基准 —— 不去读焦点的瞬时状态:
            # 焦点/悬停那套状态在被别的操作打断时可能还没更新, 读它翻页会翻错页
            c.bind("<Left>", lambda _e, k=key: self._step_tab(-1, k))
            c.bind("<Right>", lambda _e, k=key: self._step_tab(1, k))
            c.bind("<Home>", lambda _e: self._pick_tab(TABS[0][0]))
            c.bind("<End>", lambda _e: self._pick_tab(TABS[-1][0]))
        # 尾巴: 最后一个页签右边到内容区右沿（右侧提示 + 剩下的分割线）
        self.tab_tail = tk.Canvas(self.tab_bar, width=max(1, cwp - xs[-1]), height=ch,
                                  bg=T.C["bg"], highlightthickness=0, bd=0)
        self.tab_tail.place(x=xs[-1], y=0)
        self.tab_tail_geo = (xs[-1] / s, cw, max(1, cwp - xs[-1]))
        self._tab_hover = self._tab_focus_key = None
        self._draw_tabs()

    def _tab_boxes(self):
        """算每个页签的 (key, x0, x1) 逻辑坐标 —— 文字宽度量出来, 别写死。"""
        f = T.font(10, scale=self.s)
        x = 0
        out = []
        for key, title in TABS:
            w = int(round(f.measure(title) / self.s)) + 22
            out.append((key, x, x + w))
            x += w + self.TAB_GAP
        return out

    def _tab_layout(self):
        """(页签框, 画布范围) 两组逻辑坐标。画布 = 页签框左右各外扩半个间距(首块贴左沿)。"""
        boxes = self._tab_boxes()
        half = self.TAB_GAP / 2.0
        spans = [(key, 0 if i == 0 else x0 - half, x1 + half)
                 for i, (key, x0, x1) in enumerate(boxes)]
        return boxes, spans

    PAGE_PAD_TOP = 8    # 页面内容与页签条的间距(逻辑): 只留一点气 —— 左沿那条收束**不靠它让位**,
                        # 而是画在一块**覆盖层**(独立小画布, create 在页面容器之后 → 天然盖在上面),
                        # 所以页面内容既不下移也不变矮(用户建议的做法)
    TAB_H = 27          # 页签条高度(逻辑): 窄扁。比页签框(2~22)高一截, 是留给"凸线"的:
                        # 线粗的一头要**抬到页签框下面**, 否则会被相邻页签的框线盖住,
                        # 只剩颜色渐变、粗细渐变看不见(实测第一版就是这样)
    TAB_GAP = 6         # 页签之间的间距(逻辑)
    TAB_BOX = (2, 22)   # 未选中页签框的上下沿(逻辑)
    TAB_TEXT_Y = 12     # 页签文字的竖直中线(逻辑): 所有页签共用一条基线
    TAB_EXTRA = 2       # 画布比页签条多露几**物理像素**: 只留底色渐变收尾那 1 行 + 余量
                        # 再收束一小段"留地方(页面内容从 PAGE_PAD_TOP 之后才开始, 不会盖到)
    TAB_FILL_EXTRA = 1  # 选中页签的底色**多画 1 物理像素**到分割线下面那一行: 用户要的终点是
                        # "紧贴分割线、在它下面一行的背景那条像素"(渐变最后一行 = 页面第一行)
    TAB_FADE = 19.2     # 选中页签底部"融进页面"的渐变带高度(逻辑, 24 行) + 曲线 t²:
                        # 上面几乎不动(维持按钮的整体感), 往下平滑加速落进底色。行数和曲线都
                        # 按"相邻像素差值别太大"来定 —— 24 行 + t² 时最大一级只差 2~3 级(255 里),
                        # 而且"初始那一块浓的"更长(用户要"开始渐变再慢一点点、延长初始更浓的那块")
                        # 用户要的是"背景色只在最底下一行出现": 带子拉长的话, 靠底的几行都会
                        # 落到接近背景色, 看着就像背景爬上来了一截
    EDGE_TAIL = 90      # 最左页签那根竖框线**在分割线以下**再收束几行(物理像素): 上面一路满亮、
                        # 跟右边那根对称, 只在框线之外才收(用户:"超出左框线范围再收束渐变");
                        # 行数给够, 收束才像凸线那样"渐变到背景色消失"而不是砍一刀
    TAB_CROWN = 80      # 选中页签两侧"凸线"的渐变长度(逻辑): 从这里开始淡成普通细线
                        # (短了看着"骤然收掉", 用户实测反馈"延申得不够长")
    TAB_LINE_W = 1.6    # 选中页签框线粗细(逻辑; 物理像素 = 这个数 × 缩放): 125% 下 = 2px。
                        # **凸线粗的那头必须与它同粗**, 否则接不上(用户实测反馈)

    @property
    def tab_line_w(self):
        return max(2, int(round(self.TAB_LINE_W * self.s)))

    def _draw_tabs(self):
        """重画整条页签(每个页签画自己那块画布)。"""
        for key, c in self.tab_widgets.items():
            self._draw_tab(key, c)
        if getattr(self, "tab_tail", None) is not None:
            self.tab_tail.delete("tab")
            self._draw_divider(self.tab_tail, self.tab_tail_geo[0], self.tab_tail_geo[1],
                               self.tab_tail_geo[2])
            self.tab_tail.create_text(self.tab_tail.winfo_reqwidth(),
                                      int(self.TAB_TEXT_Y * self.s),
                                      anchor="e", text=TAB_TIP, fill=T.C["text_dim"],
                                      font=T.font(8, scale=self.s), tags=("tabtip",))

    def _draw_tab(self, key, c):
        """画一个页签(它自己那块画布): 先铺底下那条分割线, 再画页签本身(压在分割线上)。

        选中: 平色亮底 + 霓虹描边(**底边不封口**) + 底部一小条渐变到页面底色。
        未选中: 淡底 + 描边(悬停/聚焦时描边转亮变粗)。
        文字: 画在页签框的**正中** —— 原来多加了 1 个逻辑单位(想补偿中文字形的视觉重心),
        结果看着"离下边比离上边近"(用户实测反馈), 现在按几何中心走。
        """
        s = self.s
        c.delete("tab")
        gx0, gx1, gpx = self.tab_geo[key]
        box = {k: (x0, x1) for k, x0, x1 in self._tab_boxes()}[key]
        lx0 = int(round((box[0] - gx0) * s))
        lx1 = int(round((box[1] - gx0) * s))
        h = int(self.TAB_H * s)
        cur = self.state.get("tab") or "more"
        on = (key == cur)
        hov = (getattr(self, "_tab_hover", None) == key)
        focus = (getattr(self, "_tab_focus_key", None) == key)
        top, bot = (int(v * s) for v in self.TAB_BOX)      # 未选中页签的上下沿
        first = (self._tab_boxes()[0][0] == key)           # 是不是最左边那个页签
        self._draw_divider(c, gx0, gx1, gpx)               # 分割线/凸线在页签**下面**(先画)
        if on:
            # 选中页签: 上半段平色(比未选中的 panel 亮一档, 一眼看出在哪页), 底下
            # TAB_FADE 那一条才渐变到页面底色 —— 起始位置按用户要求"在文字下面一点点"。
            # ⚠️ 以前是**整块**竖向渐变(metal_light→panel): 又生硬、上半部分还比原来亮。
            body = T._P["metal"]
            hb = h + self.TAB_FILL_EXTRA             # 底色/渐变的底边(分割线下面那一行)
            fade_y = hb - int(self.TAB_FADE * s)
            c.create_rectangle(lx0, 0, lx1, fade_y, fill=T._hx(body), outline="",
                               tags=("tab",))
            # 终点在**分割线下面那一行**(= 页面第一行, 用户指定的第三种); 曲线 t²: 先慢后快、
            # 每一级的差值都压得很小(见 TAB_FADE 注释)
            _grad_v(c, lx0, fade_y, lx1, hb, body, T._P["bg"], ease=2)
            # 描边: 左/上/右三边一笔画完(底边**不封** —— 与两侧的凸线融合成一条, 见 _draw_divider)。
            # 粗细 = 凸线粗的那一头(tab_line_w), 少了对不上; 扣掉键盘焦点后**不能变细**,
            # 否则"选完别的页/点过别处"框线就细下去、跟凸线接不上了(用户实测反馈);
            # 聚焦时再加 1px 当焦点提示。
            # 最左边那个页签左边没地方铺横向凸线: 它的左竖线**自己往下淡掉**(见 _draw_edge_fade),
            # 不然那根线到分割线就"啪"一下断了。
            # 框线粗细**永远不变**(不随焦点加粗: 加粗会跟凸线的粗细对不上, 也容易看着像折线)。
            # 键盘焦点单独在**框外面**画一圈 1px(颜色比框色淡一点) —— 用户要的就是这个
            # ("之前那个聚焦框可以, 只是某次改坏了才有折线")。
            w = self.tab_line_w
        # 画法分两段(试了几版才对):
            #  ① **顶边 + 两侧上段 = 一条折线**: Tk 折线的接头默认是圆角的, "上面两个角"原来
            #     那个"缺一个像素的圆润"就是它做出来的 —— 换成圆头横线会两头各多半个像素、
            #     顶边还只剩一行(用户: "上框线还加了些像素")。
            #  ② **两侧下段 = 填充矩形**: 像素列/行都是整数, 左右严格对称、底端能给准。
            # 两段的像素列必须一致: 折线左边竖线在 x=lx0+0.5 → 盖 lx0、lx0+1; 右边竖线要放在
            # x=lx1-1.5 → 盖 lx1-2、lx1-1(跟右边矩形一致, 别放在 lx1-0.5 那会差一列)
            # 底端一律停在 h-2(比原来少一行 = 跟顶角一样"缺一个像素")。
            # ⚠️ Tk 的填充矩形也只覆盖"像素中心落在区间内"的行列: 想盖住 lx0..lx0+w-1, 右边界
            #    要写 lx0+w(**不是** lx0+w-1, 否则每边都少一列)；同理下边界写 h-1。
            # 顶边比原来低 1 行(y=1.5): 上面那一行留给聚焦时的外圈(不然顶边之上没有像素)
            stub = max(5, int(round(7 * s)))          # 折线画到第几行交棒给矩形
            c.create_line(lx0 + 0.5, stub, lx0 + 0.5, 1.5, lx1 - 1.5, 1.5, lx1 - 1.5, stub,
                          fill=T.C["neon"], width=w, tags=("tab",))
            # 底端: **只切最外面那一列的最后一行**(跟顶角"缺一个像素"完全对称) ——
            # 之前是把整条竖线的最后一行都切掉了, 那是"缺一整排", 用户一眼就看出多缺了。
            # 内侧那 w-1 列一直走到分割线那一行。
            # 切的是**靠里**那一列的最后一格: 外侧那列要一直走到分割线 —— 它才是跟凸线接上的
            # 那一格("贴合着框线"), 切外侧会在角上留一道缝(用户实测)
            # 最左页签的左边**不切**: 它下面接着画收束, 切了会在收束起点留一道缝
            bot_l = h if first else h - 1
            c.create_rectangle(lx0, stub, lx0 + 1, h,
                               fill=T.C["neon"], outline="", tags=("tab",))
            c.create_rectangle(lx0 + 1, stub, lx0 + w, bot_l,
                               fill=T.C["neon"], outline="", tags=("tab",))
            c.create_rectangle(lx1 - 1, stub, lx1, h,
                               fill=T.C["neon"], outline="", tags=("tab",))
            c.create_rectangle(lx1 - w, stub, lx1 - 1, h - 1,
                               fill=T.C["neon"], outline="", tags=("tab",))
            if focus:
                # 聚焦提示: 框**外面**再画一圈 1px, 颜色比框色淡一点点(用户指定)。
                # ⚠️ 用**填充矩形**而不是画线: Tk 的 1px 竖线按"像素中心"取整, x+0.5 这种坐标
                #    左面会落到前一列、右面落到后一列(实测右边就空出一格 —— 用户: "右侧有,
                #    但间隔了一条像素")。矩形给整数列, 左右一样准。
                # 上/左/右三面都画; 下端停在凸线上方那两行, 别压到凸线。
                pale = T.C["neon_pale"]
                ry1 = h - 2                            # 外圈下端(不含凸线那两行)
                # 顶角也**收 1 个像素**: 外圈顶行比两侧各缩 1 列、两侧从第 1 行才开始 ——
                # 跟里面框线那两个"圆润的角"同一个做法, 不然方的外圈会把圆角盖掉
                c.create_rectangle(lx0, 0, lx1, 1, fill=pale, outline="",
                                   tags=("tab",))              # 顶(两端各缩 1 列)
                c.create_rectangle(lx0 - 1, 1, lx0, ry1, fill=pale, outline="",
                                   tags=("tab",))              # 左(从第 1 行起)
                c.create_rectangle(lx1, 1, lx1 + 1, ry1, fill=pale, outline="",
                                   tags=("tab",))              # 右(从第 1 行起)
                # 框线顶角本来"缺一个像素"(圆润); 有了外圈以后那格被围起来, 看着就是个黑点,
                # 所以聚焦时把这两格补成框色 —— 圆润交给外圈自己收角(它已经缩了 1 列)
                c.create_rectangle(lx0, 1, lx0 + 1, 2, fill=T.C["neon"], outline="",
                                   tags=("tab",))
                c.create_rectangle(lx1 - 1, 1, lx1, 2, fill=T.C["neon"], outline="",
                                   tags=("tab",))
        else:
            # 未选中页签的聚焦框**维持原来那种**(霓虹描边 + 加粗): 它没有凸线要衔接, 不会
            # 像选中页签那样"框变粗就错位"
            oc = T.C["neon"] if focus else (T.C["neon2"] if hov else T.C["metal_light"])
            c.create_rectangle(lx0, top, lx1, bot, fill=T.C["panel"], outline=oc,
                               width=2 if focus else 1, tags=("tab",))
        if first:
            # ⚠️ 这两样画在**别的控件**上(收束在覆盖层、左边那一列在主画布), 不在本页签画布里,
            #    所以不能只放在"选中"分支里调 —— 本页签一变未选中那条分支就不执行, 旧的就
            #    留在屏幕上(踩过: 点击切走时"更多操作"左边那条单像素线残留)
            if on:
                self._edge_tail(w)
            else:
                self._hide_edge_tail()
            # 最左页签左边那一圈画不出来(页签画布左边缘之外没有像素), 在主画布上补
            self._left_ring(bool(on and focus))
        # 文字竖直中线所有页签共用一条(选中页签框比别的页签高出一截, 但字要对齐)
        c.create_text(int((lx0 + lx1) / 2), int(self.TAB_TEXT_Y * s), text=dict(TABS)[key],
                      fill=(T.C["neon"] if on else
                            (T.C["text"] if (hov or focus) else T.C["text_dim"])),
                      font=T.font(10, True, s), tags=("tab",))

    def _edge_tail(self, w):
        """最左页签那根竖框线**在分割线以下**的收束 —— 画在**覆盖层**上(不在页签画布里)。

        为什么要单独一块画布: 这条收束要往下走很长(用户要"跟右侧横向凸线差不多长"),
        画在页签画布里就得把页面内容往下推 —— 用户说得很对:"找一个可以自由重叠的组件、
        当那个线覆盖上去, 页面就不会下移"。这块画布 create 在页面容器之后, 天然盖在页面上面;
        它的底色和页面底色一致, 所以除了那条线本身看不出来。
        框线本身满亮走到自己的尽头(跟右边那根对称), 收束只在**框线范围之外**做。
        """
        s = self.s
        if self.edge_tail is None:
            return
        # 三列: 框线那两列 + 外面再一列 —— 用户要"三条像素的渐变线"(纵向凸线跟横向那条一样,
        # 越往外越短越淡)。所以整块往左挪 1 列画(覆盖层盖在页面内容之上, 但页面内容左侧留了
        # page_inset, 不会压到文字)
        # 列数 = 框线宽度 + 是否有聚焦外圈: **和左边那一列、右边那一圈保持一致** ——
        # 以前这个宽度由两个调用点各传各的, 有时 3 列有时 4 列, 看着就是"右侧多一条竖线"、
        # 按 Tab 焦点一变又变(用户实测)
        w0 = max(1, w)
        tw = w0 + (1 if getattr(self, "_tab_focus_key", None) == TABS[0][0] else 0)
        key = (tw, self.EDGE_TAIL)
        if getattr(self, "_tail_key", None) != key:
            # 颜色一路偏向分割线的浅灰(像横向凸线那样), 再淡进背景消失
            self._tail_ph = T.vfade_photo(tw, self.EDGE_TAIL, T._P["neon"],
                                          rgb_far=T._P["metal_light"])
            self._tail_key = key
        self.edge_tail.configure(width=int((tw + 1) * s), height=int(self.EDGE_TAIL))
        self.edge_tail.delete("all")
        self.edge_tail.create_image(0, 0, anchor="nw", image=self._tail_ph)
        # 对齐方式: 让**框线那两列**永远压在同一个位置, 宽度从 3 收到 2 时掉的是**最外面**那列
        # (用户实测: 之前从左边对齐, 掉的反而是里侧那列); 外面那列正好等于 _left_ring 那一列
        # ⚠️ y 必须用"两次取整相加", 和页签画布的底边**同一套算法**:
        #    画布底边 = int(tab_y*s) + int(TAB_H*s), 而 int((tab_y+TAB_H)*s) 会差 1px ——
        #    展开快速上手(tab_y 变了)时那 1px 正好露出来, 看着就是"渐变线跟框线之间隔了一行"
        self.edge_tail.place(x=int(round(T.CONTENT_X0 * s)) - (tw - w0),
                             y=int(self.tab_y * s) + int(self.TAB_H * s))

    def _left_ring(self, show):
        """最左页签左边那圈: 在主画布上补一条 1px 淡色竖线。

        页签画布紧贴内容区左边缘, 它的列 -1 在画布之外 —— 所以这一圈只能画在主画布上
        (主画布是根窗口的第一个子控件, 而内容区从 CONTENT_X0 才开始, 那一列是空着的)。
        这样最左页签左右都是"框线 + 一圈", 跟别的页签一致(用户要求)。
        """
        c = self.canvas
        c.delete("leftring")
        if not show:
            return
        s = self.s
        x = int(round((T.CONTENT_X0 - 1) * s))
        y0 = int(self.tab_y * s) + 1                      # 第 0 行留给顶圈的圆角
        y1 = int(self.tab_y * s) + int(self.TAB_H * s)    # 一直走到分割线那一行: 与下面的收束接上
        c.create_rectangle(x, y0, x + 1, y1, fill=T.C["neon_pale"], outline="",
                           tags=("leftring",))

    def _hide_edge_tail(self):
        if self.edge_tail is not None:
            self.edge_tail.place_forget()

    def _draw_divider(self, c, gx0, gx1, gpx):
        """页签条与页面区之间那条**分割线**：底下一根 1px 浅灰细线 + 选中页签两侧的"凸线"。

        两层画法(这是"看着自然"的关键)：
          ① **底**: 1px 浅灰细线, 选中页签占的那一段留空 —— 就是那条普通分割线, 保持清晰;
          ② **上**: 一张**纯霓虹色 + 透明度渐变 + 浮点粗细**的覆盖层图片(见 ui_theme.crown_photo),
             从页签的边框起往外渐渐变细、变透明, 叠在①上 —— 视觉上就是"青 → 浅灰"融进去了。
             直接插值"青→灰"两个颜色, 中间那段会发浑、还容易出色阶断层(第一版就是); 换成
             透明度叠加就干净了。粗细**不做整数量化**(量化过 2px/3px, 超采样也只剩三层台阶)。

        渐变的**速率**不是匀速: 用 ease-out(靠页签那侧变化快、往外慢慢收), 像光晕/软阴影的
        衰减 —— 看上去是一团柔和的光往两边散开, 而不是一条被裁出斜角的粗线(用户要求
        "看起来自然过渡柔和")。
        层②画在页签**下面**(调用顺序), 于是它从相邻页签底下穿过, 看着是一条连续的线。
        最左边的页签选中时左边没有地方铺横线, 就在左下角补一小段**向下淡出**的竖线
        (tail_fade), 否则那根竖框线"啪"一下就没了(用户实测反馈)。
        """
        s = self.s
        n = max(1, int(gpx))                         # **画布的实际像素宽**(见 tab_geo)
        w_max = self.tab_line_w                      # 与选中页签的框线同粗(2px @125%)
        base_y = int((self.TAB_H - 1) * s)           # 细线的位置
        cur_key = self.state.get("tab") or "more"
        sel = next((b for b in self._tab_boxes() if b[0] == cur_key), None)
        if sel:                                      # 选中页签占的像素列: 这段不画线
            gap0, gap1 = int(round((sel[1] - gx0) * s)), int(round((sel[2] - gx0) * s))
        else:
            gap0, gap1 = 10 ** 9, -1
        # 凸线要往页签底下多盖几个像素: 它的收尾是被高斯模糊抹开的, 如果正好在页签边缘收掉,
        # 那一列的透明度会被旁边的透明区拉低一截(实测只有 ~0.6), 看着就像"凸线与框线之间
        # 缺了一格/有一道缝"(用户两次提到这个位置)。多盖一点, 收尾就藏在填充和框线下面了。
        pad = max(1, self.tab_line_w // 2) + 2
        ex0, ex1 = gap0 + pad, max(gap0 + pad, gap1 - pad)
        # ① 底色那条细线(除了选中页签那一段) —— 单列宽, 不用图片也够清晰。
        #    ⚠️ 画到 b(**不是 b-1**): Tk 的平头线只覆盖"中心落在区间内"的像素, 画到 b-1 会
        #    少画最后一列 —— 每块画布右边就缺一格(用户实测:"下面那个像素似乎缺了一小块")。
        for a, b in ((0, min(n, gap0)), (max(0, gap1 + 1), n)):
            if b > a:
                c.create_line(a, base_y, b, base_y,
                              fill=T.C["metal_light"], tags=("tab",))
        # ② 凸线覆盖层
        def ramp(i):
            """这一列淡到几成(0=贴着页签, 1=已经是普通细线)。

            用**匀速**(f = x): ease-out 是"前半段就掉掉大半", 看着"收得有点快"
            (用户实测反馈); 匀速 + 加长(TAB_CROWN)之后, 每一列的变化量都一样小,
            既没有明显台阶, 也是慢慢收进去的。
            """
            if ex0 <= i <= ex1:
                return None                          # 选中页签下面(含半个框线): 不画
            d = 0.0 if sel is None else max(0.0, (ex0 - i) if i < ex0 else (i - ex1)) / s
            return min(1.0, d / float(self.TAB_CROWN))

        def thick(i):
            # 粗细**慢收**(x 的 0.7 次方): 贴着页签那一段一直是 2px, 出去很远才慢慢变细 ——
            # 这样"粗的那截"足够长, 不会看着像突然收掉
            x = ramp(i)
            return 0.0 if x is None else w_max - (w_max - 1.0) * (x ** 0.7)

        def alpha(i):
            x = ramp(i)
            return 0.0 if x is None else 1.0 - x

        band_h = int(round(w_max))
        key = (n, band_h, ex0, ex1)
        if getattr(c, "_crown_key", None) != key:    # 换页/换布局才重画, 悬停时直接复用
            c._crown_ph = T.crown_photo(n, band_h, int(round(w_max)) - 1, thick, alpha,
                                        T._P["neon"])
            c._crown_key = key
        c.create_image(0, base_y - (int(round(w_max)) - 1), anchor="nw",
                       image=c._crown_ph, tags=("tab",))

    # ---- 页签的键盘 ----
    def _tab_focus(self, key, on):
        """焦点进/出某个页签: 只重画受影响的页码(焦点框 = 描边加粗转霓虹)"""
        self._tab_focus_key = key if on else None
        for k in (key, getattr(self, "_tab_last_focus", None)):
            c = self.tab_widgets.get(k)
            if c is not None:
                self._draw_tab(k, c)
        self._tab_last_focus = key if on else None
        # 最左页签那条"左边一列 + 下面收束"的列数跟焦点有关, 焦点一变要重画一次
        if (self.state.get("tab") or "more") == TABS[0][0]:
            self._edge_tail(self.tab_line_w)

    def _tab_activate(self):
        """回车/空格 = 切到**当前拿到焦点的那个页签**(Tab 走到未选中的页签后按它就切页)"""
        key = getattr(self, "_tab_focus_key", None)
        if key:
            self._select_tab(key)
        return "break"

    def _pick_tab(self, key):
        """Home/End: 切到第一个/最后一个页签(焦点跟着走)"""
        self._select_tab(key)
        c = self.tab_widgets.get(key)
        if c is not None:
            try:
                c.focus_set()
            except Exception:
                pass
        return "break"

    def _on_tab_hover(self, ev, key):
        """悬停: 只有鼠标真在**页签框**上才算(画布比框宽出半个间距, 那部分不算)"""
        gx0, _gx1, _gpx = self.tab_geo[key]
        box = {k: (x0, x1) for k, x0, x1 in self._tab_boxes()}[key]
        x = gx0 + ev.x / self.s
        hit = key if box[0] <= x <= box[1] else None
        if hit != getattr(self, "_tab_hover", None):
            self._tab_hover = hit
            self._draw_tabs()

    def _step_tab(self, delta, base=None):
        """左右方向键翻页: 以**按键来自哪个页签**为基准(没传就看焦点、再看当前页), 焦点跟过去"""
        keys = [k for k, _t in TABS]
        base = base or getattr(self, "_tab_focus_key", None) or self.state.get("tab") or "more"
        i = keys.index(base) if base in keys else 0
        self._pick_tab(keys[(i + delta) % len(keys)])

    def _on_tab_hover_clear(self):
        if getattr(self, "_tab_hover", None):
            self._tab_hover = None
            self._draw_tabs()

    def _on_tab_click(self, ev, key):
        """点页签框才切页(画布左右各多出半个间距, 点在缝里不理)"""
        gx0, _gx1, _gpx = self.tab_geo[key]
        box = {k: (x0, x1) for k, x0, x1 in self._tab_boxes()}[key]
        x = gx0 + ev.x / self.s
        if box[0] <= x <= box[1]:
            self._select_tab(key)
            try:
                self.tab_widgets[key].focus_set()      # 点了也把键盘焦点挪过来
            except Exception:
                pass

    def _select_tab(self, key, save=True, initial=False):
        """切换到某一页（并记住；日志页切回来时自动滚到底）。"""
        s = self.s
        if key not in self.pages:
            key = "more"
        for k, p in self.pages.items():
            if k == key:
                p.pack(fill="both", expand=True,
                       padx=(int(self.page_inset * s), 0),      # 给左沿收束的覆盖层让出列
                       pady=(int(self.PAGE_PAD_TOP * s), 0))    # 与页签条之间留点气
            else:
                p.pack_forget()
        self.state["tab"] = key
        if save:
            save_state(self.state)
        if hasattr(self, "tab_bar"):
            self._draw_tabs()
        if getattr(self, "edge_tail", None) is not None:
            if key == TABS[0][0]:                      # 只有最左边那个页签有这根收束
                self._edge_tail(self.tab_line_w)
            else:
                self._hide_edge_tail()
        if key == "log" and hasattr(self, "log_text"):
            try:
                self.log_text.see("end")
            except Exception:
                pass

    # ---- 旧折叠区接口的兼容包装（内部还有几处按"打开日志页"在调）----
    def _open_fold(self, key):
        self._select_tab(key)

    def _on_fold(self, *_a):
        pass

    def _build_bottom_bar(self, root, x0, cw, y):
        """最下面常驻三个说明类按钮（使用说明 / 免责声明 / 杀软白名单，用户要求）。

        固定贴在窗口底部（不随折叠区上下跑），用强调色以区别于普通操作按钮。
        """
        s = self.s
        bw, bh = T.BTN_SIZES["wide"]
        gap = 30                       # 三个常驻按钮之间留宽松一点(原来 10 太挤, 用户反馈)
        total = bw * len(BOTTOM_BTNS) + gap * (len(BOTTOM_BTNS) - 1)
        bx = x0 + (cw - total) // 2
        acts = {"help": self.show_help,
                "disclaimer": self.show_disclaimer,
                "antivirus": self.show_antivirus}
        self.bottom_btns = []
        for i, (label, tip, act) in enumerate(BOTTOM_BTNS):
            # 这三枚是常驻的"说明类"入口, 用户要求**加粗 + 稍大**做强调(accent 已经让它们
            # 用霓虹青 + 加粗, font_size 再抬一档: 9→11, 比主按钮的名字那行还略大一点)
            b = T.ImgButton(root, label, kind="wide", command=acts[act], skin=self.skin,
                            scale=s, bg=T.C["bg"], tooltip=tip, accent=True, font_size=11)
            b.place(x=int((bx + i * (bw + gap)) * s), y=int(y * s))
            self.bottom_btns.append(b)
        return self.bottom_btns

    def _hover_conn_hint(self, on):
        """状态行那句"初次使用…": 悬停变亮 + 弹出详细说明 + 手型光标（点它是打开使用说明）

        光标只能挂在**画布**上(`create_text` 不支持 -cursor 选项, Tk 会报
        "unknown option -cursor")，所以进/出这一项时切换整块主画布的 cursor。
        """
        try:
            self.canvas.itemconfigure(self._hint_item,
                                      fill=T.C["neon"] if on else T.C["warn"])
            self.canvas.configure(cursor="hand2" if on else "")
        except Exception:
            pass
        self._tip(CONN_HINT_TIP if on else None)

    def _bring_to_front(self):
        """把窗口提到最前一次。

        提权起来的实例（或"启动即提权"后由 helper 拉起的那个）**不被允许抢前台**，
        窗口可能就静静躺在别的窗口后面、只在任务栏闪一下——用户看到的就是"点了是没反应"。
        用"短暂置顶 + SetForegroundWindow"确保他看得见，1.2 秒后恢复常态（不长期置顶）。
        """
        r = self.root
        for step in (r.deiconify, r.lift, r.focus_force,
                     lambda: r.attributes("-topmost", True),
                     lambda: ctypes.windll.user32.SetForegroundWindow(self.hwnd)):
            try:
                step()
            except Exception:
                pass
        if not os.environ.get("SC2_UNCENSOR_UI_TEST"):        # 测试截图要保持置顶
            self.root.after(1200, lambda: self._topmost_off())

    def _topmost_off(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _initial_pos(self, W, H):
        geom = os.environ.get("SC2_UNCENSOR_UI_GEOM")
        if geom:
            try:
                x, y = (int(v) for v in geom.split(","))
                return x, y
            except Exception:
                pass
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = self.state.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            if -20 <= x <= sw - 120 and -20 <= y <= sh - 120:
                return x, y
        return (sw - W) // 2, max(0, (sh - H) // 2 - 30)

    # ---------- 窗口外观 / 最小化 ----------
    def _frameless(self, W, H, x, y):
        """去掉原生标题栏与边框，但**保留 WS_SYSMENU / WS_MINIMIZEBOX**。

        这样窗口仍是普通窗口：最小化交给系统管，任务栏按钮正常保留、点一下就能还原。
        之前用 overrideredirect(True)（WS_POPUP），最小化会退化成桌面左下角一个没有
        任务栏按钮的小方块，任务栏里也再找不到它（用户实测反馈）。
        """
        u = ctypes.windll.user32
        self.hwnd = u.GetParent(self.root.winfo_id())
        GWL_STYLE = -16
        WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
        WS_SYSMENU, WS_MINIMIZEBOX = 0x00080000, 0x00020000
        st = u.GetWindowLongW(self.hwnd, GWL_STYLE)
        st = (st & ~(WS_CAPTION | WS_THICKFRAME)) | WS_SYSMENU | WS_MINIMIZEBOX
        u.SetWindowLongW(self.hwnd, GWL_STYLE, st)
        SWP_FRAMECHANGED, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x20, 0x4, 0x10, 0x40
        u.SetWindowPos(self.hwnd, None, x, y, W, H,
                       SWP_FRAMECHANGED | SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _win_state(self):
        """给自动化用例看的窗口状态: (是否最小化, 是否可见, 宽, 高, style, exstyle)"""
        u = ctypes.windll.user32
        rect = wt.RECT()
        u.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return (u.IsIconic(self.hwnd), u.IsWindowVisible(self.hwnd),
                rect.right - rect.left, rect.bottom - rect.top,
                u.GetWindowLongW(self.hwnd, -16), u.GetWindowLongW(self.hwnd, -20))

    def _log_win_state(self, when):
        print("[界面] %s iconic=%d vis=%d rect=%dx%d style=0x%X ex=0x%X"
              % ((when,) + self._win_state()))

    def _minimize(self):
        """最小化：窗口是"去掉标题栏的普通窗口"，直接用系统最小化（任务栏按钮保留）"""
        try:
            self.root.iconify()
        except Exception:
            try:
                ctypes.windll.user32.ShowWindow(self.hwnd, 6)      # SW_MINIMIZE
            except Exception as e:
                self._set_hint("warn", f"最小化失败：{e}")

    def _test_minimize(self):
        self._log_win_state("最小化前")
        self._minimize()
        self.root.after(1200, self._test_restore)

    def _test_restore(self):
        self._log_win_state("最小化后")
        try:
            ctypes.windll.user32.ShowWindow(self.hwnd, 9)          # SW_RESTORE(等价点任务栏)
        except Exception:
            self.root.deiconify()
        self.root.after(1000, lambda: self._log_win_state("还原后"))

    def _test_press_hotkey(self, name="status"):
        """自动化用: 往快捷键线程投一条 WM_HOTKEY, 等价于按了该功能的键（不碰真键盘）"""
        try:
            reg = core._REG_BY_NAME.get(name)
            ident = reg[0] if reg else next((i for i, n in core._NAME_BY_ID.items()
                                            if n == name), None)
            if ident is None:
                print("[界面] 快捷键用例: %s 找不到 ident" % name)
                return
            ctypes.windll.user32.PostThreadMessageW(core._MAIN_TID, 0x0312, ident, 0)
            print("[界面] 快捷键用例: 已投递 %s 的 WM_HOTKEY(ident=%s)" % (name, ident))
        except Exception as e:
            print(f"[界面] 快捷键用例失败: {e}")

    def _test_notice_button(self):
        """点一下弹窗的最后一个按钮(通常是"关闭/知道了")，看它到底关不关窗"""
        dlg = self._last_notice
        if dlg is None or not dlg.winfo_exists():
            print("[界面] 弹窗按钮测试: 弹窗不存在")
            return
        btn = dlg._btns[-1]
        w, h = btn.winfo_width(), btn.winfo_height()
        try:
            btn.event_generate("<ButtonPress-1>", x=w // 2, y=h // 2)
            btn.event_generate("<ButtonRelease-1>", x=w // 2, y=h // 2)
        except Exception as e:
            print(f"[界面] 弹窗按钮测试: 事件投递失败 {e}")
            return
        self.root.after(400, lambda: print(
            "[界面] 弹窗按钮测试: 点击后弹窗%s"
            % ("已关闭" if not dlg.winfo_exists() else "仍然开着(按钮无效!)")))

    def _add_win_btn(self, kind, x, y, tip):
        s = self.s
        ph = self.skin.img(f"btn_win_{kind}", (18, 18))
        item = self.canvas.create_image(int(x * s), int(y * s), anchor="nw", image=ph,
                                        tags=("winbtn", f"win_{kind}"))
        self.canvas.tag_bind(item, "<Enter>", lambda _e, k=kind: self._hover_win(k, True))
        self.canvas.tag_bind(item, "<Leave>", lambda _e, k=kind: self._hover_win(k, False))
        self.canvas.tag_bind(item, "<Button-1>", lambda _e, k=kind: self._click_win(k))
        self.canvas.tag_bind(item, "<Enter>", lambda _e, t=tip: self._tip(t), add="+")
        self.canvas.tag_bind(item, "<Leave>", lambda _e: self._tip(None), add="+")
        return item

    def _hover_win(self, kind, on):
        ph = self.skin.img("btn_win_" + kind, (18, 18), dim=0.25 if on else 0.0)
        if ph:
            self.canvas.itemconfigure(self.winbtns[kind], image=ph)

    def _click_win(self, kind):
        if kind == "close":
            self.quit_app()
        elif kind == "min":
            self._minimize()

    def _tip(self, text):
        """标题栏按钮的极简悬停提示（只显示一小行）"""
        if getattr(self, "_tipwin", None):
            try:
                self._tipwin.destroy()
            except Exception:
                pass
            self._tipwin = None
        if not text:
            return
        s = self.s
        w = tk.Toplevel(self.root)
        w.wm_overrideredirect(True)
        w.attributes("-topmost", True)
        tk.Label(w, text=text, bg=T.C["metal_dark"], fg=T.C["text"], font=T.font(9, scale=s),
                 padx=int(6 * s), pady=int(3 * s)).pack()
        # 跟随鼠标（以前写死在窗口左侧 410px 处，鼠标停在标题栏右边的按钮上时，
        # 提示会跑到左边去——用户实测）。放光标右下方一点，并夹在屏幕内。
        w.update_idletasks()
        cx, cy = self._cursor_pos()
        tw, th = w.winfo_reqwidth(), w.winfo_reqheight()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x = min(max(0, cx + int(8 * s)), max(0, sw - tw - 4))
        y = min(max(0, cy + int(18 * s)), max(0, sh - th - 4))
        w.geometry(f"+{x}+{y}")
        self._tipwin = w

    def _bind_drag(self):
        """拖动窗口：绑在皮肤画布上的三个鼠标事件。

        不用 `root.geometry()` 逐帧搬窗口——那会让 Tk 每帧重新布局 + 重画整张皮肤底图(725×750)，
        大窗口拖起来就能看到拖影/交叠（用户实测反馈）；改用 Win32 的 SetWindowPos 直接搬，
        Tk 不做布局也不重画，画面由 DWM 合成，还顺带跟手。
        """
        self.canvas.bind("<ButtonPress-1>", self._on_drag_press)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_release)

    def _cursor_pos(self):
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _on_drag_press(self, e):
        cur = self.canvas.find_withtag("current")
        if cur and "winbtn" in self.canvas.gettags(cur[0]):
            return                      # 点在标题栏按钮上: 不当作拖动
        # 增量统一用 GetCursorPos 的物理像素：Tk 事件的 x_root 与 winfo_x 在
        # 125% 缩放下不是同一套单位（实测位移会差 1.25 倍，拖起来"比鼠标快"）
        cx, cy = self._cursor_pos()
        rect = wt.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self._drag = (cx, cy, rect.left, rect.top)

    def _on_drag_move(self, e):
        if not getattr(self, "_drag", None):
            return
        cx, cy = self._cursor_pos()
        nx = self._drag[2] + (cx - self._drag[0])
        ny = self._drag[3] + (cy - self._drag[1])
        self._drag_pos = (nx, ny)
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        try:
            ctypes.windll.user32.SetWindowPos(self.hwnd, None, nx, ny, 0, 0,
                                              SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            self.root.geometry(f"+{nx}+{ny}")

    def _on_drag_release(self, _e):
        if getattr(self, "_drag", None) and getattr(self, "_drag_pos", None):
            # 存 Win32 物理坐标(与 SetWindowPos 同一套), 下次启动按这个位置摆
            self.state["pos"] = [int(self._drag_pos[0]), int(self._drag_pos[1])]
            save_state(self.state)
        self._drag = None

    def _taskbar(self):
        """普通窗口本来就有任务栏按钮；这里只保证 ex-style 里没有 TOOLWINDOW
        （无标题栏窗口在某些环境会被当成工具窗口，那样任务栏就看不到它了）"""
        try:
            GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x40000, 0x80
            ex = ctypes.windll.user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                self.hwnd, GWL_EXSTYLE, (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
        except Exception:
            pass

    # ---------- 页面内容 ----------
    # （页签本身在 _build_tabs/_select_tab；这里是各页的内容填充）

    def _btn(self, parent, name, kind="big"):
        """按名字建一个操作按钮（文案/键位/悬停说明都取自统一的地方）"""
        b = T.ImgButton(parent, OP_LABEL[name], self.effective_key(name), kind=kind,
                        command=lambda n=name: self.act(n), skin=self.skin, scale=self.s,
                        bg=T.C["bg"], tooltip=lambda n=name: _cfg_tip(f"hotkeys.{n}"))
        setattr(self, "btn_" + name, b)
        return b

    def _grid(self, parent, items, cols=2, kind="big", padx=6, pady=4):
        """把按钮按 cols 列均匀铺开：每列等宽、按钮居中（别再全挤在左边）"""
        for i, name in enumerate(items):
            r, c = divmod(i, cols)
            parent.columnconfigure(c, weight=1, uniform="gridbtn")
            self._btn(parent, name, kind=kind).grid(
                row=r, column=c, padx=int(padx * self.s), pady=int(pady * self.s), sticky="")

    def _fill_more(self, body):
        # padx 从 6 收到 2：240 宽的两列在 500 宽里正好排满，6 会把右列挤出边框
        self._grid(body, MORE_ITEMS, cols=2, kind="big", padx=2)

    def _fill_hotkeys(self, body):
        s = self.s
        self.hk_frames = {}
        grid = tk.Frame(body, bg=T.C["bg"])
        grid.pack(fill="x")
        for i, name in enumerate(OP_ORDER):
            # 定宽单元格 + 两端对齐：名字左、键位靠右、修改按钮最右，长键位不会压到按钮
            cell = tk.Frame(grid, bg=T.C["bg"], width=int(246 * s), height=int(36 * s))
            cell.pack_propagate(False)
            cell.grid(row=i // 2, column=i % 2, sticky="w", pady=int(2 * s))
            T.ImgButton(cell, "修改", kind="mini", command=lambda n=name: self.edit_hotkey(n),
                        skin=self.skin, scale=s, bg=T.C["bg"],
                        tooltip=lambda n=name: _cfg_tip(f"hotkeys.{n}")).pack(side="right")
            lbl = tk.Label(cell, text="—", bg=T.C["bg"], fg=T.C["neon2"],
                           font=T.font(9, scale=s), anchor="e")
            lbl.pack(side="right", padx=(int(2 * s), int(4 * s)))
            T.ToolTip(lbl, lambda n=name: self.hk_tip(n), scale=s)
            # 名称用 8 号基准：字放大后 "手动关闭反和谐修改"(9 字) + 键位 + 修改按钮
            # 会在 246 宽的格子里挤住（实测）
            tk.Label(cell, text=OP_LABEL[name], bg=T.C["bg"], fg=T.C["text"],
                     font=T.font(8, scale=s), anchor="w").pack(side="left")
            self.hk_frames[name] = lbl
        tk.Label(body, text=TX.HOTKEY_PAGE_TIP,
                 bg=T.C["bg"], fg=T.C["text_dim"], font=T.font(9, scale=s), justify="left",
                 anchor="w", wraplength=int(490 * s)).pack(fill="x", pady=(int(6 * s), 0))

    def hk_tip(self, name):
        state = ("已暂停" if not core._hotkeys_enabled and name != "hotkey_master"
                 else "正在生效" if name in {n for _i, _m, _v, n, _k in core._REG_OK}
                 else "没注册上（键位被别的软件占用，或快捷键已暂停）")
        return f"{OP_LABEL.get(name, name)}：{state}\n\n{_cfg_tip('hotkeys.' + name)}"

    def _fill_settings(self, body):
        s = self.s
        self.set_vars = {}
        self.ui_vars = {}
        grid = tk.Frame(body, bg=T.C["bg"])
        grid.pack(fill="x")
        # (是核心配置吗, 键名, 显示名)；"游戏连接提示音"是界面自己的开关，放最前面
        # （跟其他默认勾上的排在一起）。
        # ⚠️ 每项必须新建自己的 BooleanVar：曾复用循环变量 v，结果它正好是最后一个
        #    项(调试输出)的变量 → 两个复选框共用一个变量，勾一个另一个跟着变(用户实测)。
        items = [(False, "connect_beep", CONNECT_BEEP["label"]),
                 (True, "beep", "提示音"), (True, "log", "写日志"),
                 (True, "forward_keys", "按键转发"), (True, "check_version", "版本核对"),
                 (True, "admin", "管理员启动"), (True, "debug", "调试输出")]
        for i, (is_cfg, key, label) in enumerate(items):
            if is_cfg:
                var = tk.BooleanVar(value=bool(core.CFG.get(key, False)))
                cmd = lambda k=key: self._toggle_cfg(k)
                # 勾选框的悬停说明用面向用户的 SETTING_TIPS(白话), 不搬 config.json 的
                # _说明(那是给改 JSON 的人看的, 写着 "true=…"); 没写到的项才回退
                tip = lambda k=key: SETTING_TIPS.get(k) or _cfg_tip(k)
                self.set_vars[key] = var
            else:
                var = tk.BooleanVar(value=bool(self.state.get("connect_beep", True)))
                cmd = self._toggle_beep_on_connect
                tip = lambda: CONNECT_BEEP["tip"]
                self.ui_vars[key] = var
            # highlightcolor: Tk 只在**该控件有焦点时**用这个颜色画一圈 —— 键盘 Tab 走查
            # 时能看见焦点在哪(无边框自绘窗口里 Tk 默认的焦点框看不见, 用户要求补上)
            cb = tk.Checkbutton(grid, text=label, variable=var, command=cmd,
                                bg=T.C["bg"], fg=T.C["text"], selectcolor=T.C["metal_dark"],
                                activebackground=T.C["bg"], activeforeground=T.C["neon"],
                                font=T.font(10, scale=s), anchor="w",
                                highlightthickness=1, highlightbackground=T.C["bg"],
                                highlightcolor=T.C["neon"], bd=0)
            bind_check_key(cb)       # 空格/回车都能切(Tk 默认只认空格, 见 bind_check_key)
            grid.columnconfigure(i % 3, weight=1, uniform="setchk")
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=int(6 * s), pady=int(2 * s))
            T.ToolTip(cb, tip, scale=s)
        # 三个按钮**放进同一个 grid 的第 4 行**: 与上面三列勾选框共用列宽、左对齐 ——
        # 这样按钮左缘与勾选框首字对齐, 整页成一张"三列网格"(按钮另起一行的居中排布会看着散)
        for i, (text, cmd) in enumerate((("配置文件夹", self.open_data_dir),
                                        ("编辑配置", self.edit_config),
                                        ("管理员重启", self.restart_as_admin))):
            b = T.ImgButton(grid, text, kind="small", command=cmd, skin=self.skin, scale=s,
                            bg=T.C["bg"])
            b.grid(row=3, column=i, sticky="w", padx=int(6 * s), pady=(int(10 * s), 0))

    def _fill_log(self, body):
        s = self.s
        wrap = tk.Frame(body, bg=T.C["bg"])
        wrap.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, style="TScrollbar", orient="vertical")
        sb.pack(side="right", fill="y")
        self.log_text = tk.Text(wrap, height=4, wrap="word", bg=T.C["metal_dark"],
                                fg=T.C["text"], font=T.font(9, scale=s, mono=True), relief="flat",
                                padx=int(6 * s), pady=int(4 * s), highlightthickness=1,
                                highlightbackground=T.C["metal_light"],
                                highlightcolor=T.C["neon"], insertwidth=0)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.configure(command=self.log_text.yview)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("err", foreground=T.C["bad"])
        self.log_text.tag_configure("ok", foreground=T.C["good"])
        self.log_text.tag_configure("warn", foreground=T.C["warn"])
        self.log_text.tag_configure("dim", foreground=T.C["text_dim"])
        self.log_text.configure(state="disabled")
        self.autoscroll = tk.BooleanVar(value=True)
        # 一行 4 个工具按钮：放在单独的 Frame 里用 grid 均分
        # （同一个父容器不能又 pack 又 grid；"使用说明/免责声明/杀软白名单"已挪到窗口底部常驻）
        bar = tk.Frame(body, bg=T.C["bg"])
        bar.pack(fill="x", pady=(int(6 * s), 0))
        rows = ((("清空", self.clear_log), ("复制全部", self.copy_log),
                 ("日志文件", self.open_log_file), ("名词说明", self.show_terms)),)
        for ri, row_items in enumerate(rows):
            for ci, (text, cmd) in enumerate(row_items):
                b = T.ImgButton(bar, text, kind="small", command=cmd, skin=self.skin,
                                scale=s, bg=T.C["bg"])
                bar.columnconfigure(ci, weight=1, uniform="logbtn")
                b.grid(row=ri, column=ci, pady=(int(6 * s) if ri else 0, 0), padx=int(4 * s))

    # ---------- 核心启动/日志 ----------
    def _start_core(self):
        if os.environ.get("SC2_UNCENSOR_UI_NO_CORE"):
            # 界面开发/自动化用: 完全不碰核心(不注册快捷键、不连游戏、不占单实例锁)。
            # 这样即使玩家自己那个实例正在跑, 也能把界面单独起起来看效果。
            self.core_ready = True
            print("[界面] (测试模式: 不启动核心主循环)")
            return
        no_hotkeys = bool(os.environ.get("SC2_UNCENSOR_UI_NO_HOTKEYS"))   # 测试用: 不抢用户按键
        print(f"[界面] 启动: pid={os.getpid()} 管理员={self.is_admin()} "
              f"提权标记={self._elevating} 数据目录={DATA_DIR}")

        def launch():
            threading.Thread(target=core.main,
                             kwargs={"register_hotkeys": not no_hotkeys}, daemon=True).start()

        launch()

        # 只等一次就绪, 不重试。两点理由(2026-09-15 审查时删掉了原来的重试分支):
        #  · "旧实例还没死透"这种情况本来就有兜底: 提权重启起的新实例由
        #    _wait_other_instances() 先等旧实例退出, 等干净了才启动核心;
        #  · 重试也没用: 核心的 _single_instance_check() 判"已存在"时, 本进程手里已经
        #    攥着那个互斥体的句柄(实测同一进程再调 CreateMutexW 永远返回 183),
        #    所以"在本进程里重跑一次 main()"不可能成功。
        #  (原来那条重试分支靠 SC2_UNCENSOR_UI_RESTART 环境变量, 而全仓没有任何地方
        #   设置它 —— 是条永远走不到的死路。)

        def check():
            """等核心就绪信号。⚠️ 这是**工作线程**: 只能碰核心状态和队列,
            绝不能碰控件/弹窗(实测踩过: 核心起得比 mainloop 快时, 这里直接
            `self._set_hint(...)` 会抛 "RuntimeError: main thread is not in main
            loop", 就绪提示与键位刷新静默失效)。要动界面就投队列给 _pump。

            ⚠️ **别只等一次短的**: 冷启动 / 杀软扫描时核心可能要好几秒才注册完快捷键
            (2026-09-15 实测到一次超过原来 2.5s 的上限), 那样 UI 会误判成"快捷键注册
            线程没起来"、弹窗 + `core_dead=True` —— 而 `core_dead` 一置位轮询也停了,
            整个界面就废在那儿, 用户只能重开。现在改成: **等到就绪**, 或者**确认核心
            已经退出**(_MAIN_DONE, 例如单实例冲突)才报错; 上限 30 秒兜底。
            """
            t0 = time.time()
            while time.time() - t0 < 30.0:
                if core._HOTKEY_READY.wait(0.3):
                    self.core_ready = True
                    self.q.put(("hint", ("info", HINT["ready"])))
                    self.q.put(("refresh_hk", None))
                    return
                # _MAIN_DONE 可能是上一次 main() 留下的, 所以给 1 秒宽限再信它
                if time.time() - t0 > 1.0 and core._MAIN_DONE.is_set():
                    break
            self.core_dead = True
            self.q.put(("core_dead", core._MAIN_FAILED))
        threading.Thread(target=check, daemon=True).start()

    def _on_core_dead(self, reason):
        """核心没能起来(在 Tk 线程里弹说明): 单实例冲突 / 快捷键线程没就绪"""
        if reason == "single_instance":
            others = self._other_instances()
            extra = (NOTICE["busy_others"] % "\n".join(others) if others
                     else NOTICE["busy_none"])
            self.show_notice(NOTICE["busy_title"], NOTICE["busy_body"] % extra,
                             quit_after=True)
        else:
            self.show_notice(NOTICE["no_thread_title"], NOTICE["no_thread_body"])

    def _other_instances(self):
        """找出还在跑的其它本工具实例（含打包的 exe），好在提示里告诉用户该关谁"""
        me = os.getpid()
        out = []
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "cmdline", "exe"]):
                try:
                    if proc.info["pid"] == me:
                        continue
                    cl = " ".join(proc.info.get("cmdline") or [])
                    nm = (proc.info.get("name") or "")
                    if "ui_main" in cl or nm.lower().startswith("sc2uncensor"):
                        out.append(f"  · {nm}（pid {proc.info['pid']}）")
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _seed_log(self):
        """把核心启动时（界面接管日志之前）已经写进文件的内容补进面板"""
        try:
            with open(core.LOG_PATH, encoding="utf-8", errors="replace") as f:
                for line in f.read().splitlines()[-200:]:
                    if line.strip():
                        self._log_line(line, tag="dim")
        except Exception:
            pass

    # ---------- 状态轮询（1Hz） ----------
    def _poll(self):
        if not self.core_dead and not self.poll_busy:
            self.poll_busy = True
            threading.Thread(target=self._poll_worker, daemon=True).start()
        self.root.after(self.poll_gap, self._poll)

    def _poll_worker(self):
        if os.environ.get("SC2_UNCENSOR_UI_NO_CORE"):
            self.q.put(("snap", self._fake_snapshot()))
            return
        try:
            snap = core.post_ui_call(self._snapshot, timeout=0.5)
            self.q.put(("snap", snap))
        except TimeoutError:
            pass                          # 上一次操作还没跑完：这一拍不刷就行
        except Exception as e:
            self.q.put(("snap_err", str(e)))
        finally:
            self.poll_busy = False

    def _fake_snapshot(self):
        """假快照：只给界面开发/自动化看效果用（不读任何真实内存）"""
        try:
            keys = {n: k for _i, _m, _v, n, k in core._ALL_DEFS}
            reg = [n for _i, _m, _v, n, _k in core._REG_OK]
        except Exception:
            keys, reg = {}, []
        if not keys:
            keys = {n: k for n, k in core.CFG["hotkeys"].items()}
            reg = list(keys)
        return {
            "attached": True, "why": "", "nop": "nop", "store": 0, "r": 1,
            "ver": "5.0.16.97579", "keys": keys, "reg": reg,
            "paused": os.environ.get("SC2_UNCENSOR_UI_FAKE") == "paused",
            "hotkey_busy": False, "ready": True,
        }

    def _snapshot(self):
        """在快捷键线程里跑的只读快照（绝对不能碰 Tk 控件）"""
        if os.environ.get("SC2_UNCENSOR_UI_FAKE"):        # 仅供界面开发/截图看效果
            return self._fake_snapshot()
        try:
            pm, _base = core.get_pm()
            attached = pm is not None
            why = "" if attached else core._no_game_msg()
            if attached:
                nop, store, r = core.read_state()
            else:
                nop, store, r = "??", -1, -1
        except Exception as e:
            attached, why, nop, store, r = False, f"读取失败：{e}", "??", -1, -1
        return {
            "attached": attached, "why": why, "nop": nop, "store": store, "r": r,
            "ver": core._DISPLAY_VER,
            "keys": {n: k for _i, _m, _v, n, k in core._ALL_DEFS},
            "reg": [n for _i, _m, _v, n, _k in core._REG_OK],
            "paused": not core._hotkeys_enabled,
            "hotkey_busy": bool(getattr(core, "_BEEP_OP", False)),
            "ready": core._HOTKEY_READY.is_set(),
        }

    # ---------- 界面消息泵 ----------
    def _pump(self):
        # ⚠️ 顺序要紧: **先处理界面事件队列(self.q), 再抽核心日志(LOGQ)**。
        #    两者在同一次 tick 里都可能改"提示区": 比如核心刚就绪的 ("hint", 就绪)
        #    和"按快捷键查状态"的日志行("[按键] status" → 提示区改成状态)同时到齐时,
        #    先 LOGQ 后 self.q 会让"就绪"把刚显示出来的状态**盖掉** —— 现象就是
        #    "按了快捷键, 提示区还是就绪"(2026-09-15 实测, 偶发: 只有落在同一 tick 才撞上)。
        #    日志行反映的是更晚发生的操作结果, 所以事件队列先、日志后。
        n = 0
        while n < 400:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                if kind == "log":
                    self._log_line(payload)
                elif kind == "snap":
                    self._apply_snapshot(payload)
                elif kind == "snap_err":
                    self.lamp_conn.set("bad", "未连接 · " + payload[:40])
                    self._conn_tip = payload
                elif kind == "op_done":
                    self._op_done(*payload)
                elif kind == "hk_done":
                    self._hk_done(*payload)
                elif kind == "hint":
                    self._set_hint(*payload)
                elif kind == "refresh_hk":       # 核心就绪: 刷新键位显示(工作线程只投队列)
                    self.refresh_hotkeys()
                elif kind == "core_dead":        # 核心起不来: 弹窗说明(必须在 Tk 线程)
                    self._on_core_dead(payload)
            except Exception as e:
                # 也 print 一份: _log_line 只写"界面面板", 而不落盘/不进 stdout ——
                # 排查自动化用例时等于把异常吞了(2026-09-15 就吃过这个亏)
                self._log_line(f"[界面] 内部错误: {e}", tag="err")
                print(f"[界面] 内部错误(消息泵): {e!r}")
        # 再抽干核心 print 的实时日志队列（LOGQ）。
        # 注: 这个队列以前没被读 —— 日志面板只显示启动时从文件补的那几行, 核心运行期的
        #     日志(每次操作、按键)都进不了面板, 靠日志行驱动的界面反应也就全失效了。
        while True:
            try:
                _kind, _line = self.logq.get_nowait()
            except queue.Empty:
                break
            try:
                self._log_line(_line)
            except Exception:
                pass
        self.root.after(120, self._pump)

    def _log_line(self, line, tag=None):
        _require_tk_thread("_log_line")
        if not hasattr(self, "log_text"):
            return
        if line == getattr(self, "_last_log", None):   # 启动横幅"文件+实时"两条路会有重复
            return
        self._last_log = line
        if tag is None:
            if "[错误]" in line or "异常" in line or "Traceback" in line:
                tag = "err"
            elif "[提示]" in line:
                tag = "warn"
            elif "完成!" in line or "OK" in line:
                tag = "ok"
            else:
                tag = None
        # 按了"显示当前状态"快捷键时，除了日志也给提示区一句话（否则只往折叠着的日志里写，
        # 看着就像"按了没反应"——实测反馈）
        if "[按键] status" in line and self._alive():
            nop = self.lamps["mem"].label.cget("text").split("·")[-1].strip()
            opt = self.lamps["opt"].label.cget("text").split("·")[-1].strip()
            act = OP_STATE[self.op_state][1].split("·")[-1].strip()
            self._set_hint("info", HINT["status_done"] % (nop, opt, act))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", tag or ())
        total = int(self.log_text.index("end-1c").split(".")[0])
        if total > 2000:                       # 面板只留最近 2000 行，别无限涨
            self.log_text.delete("1.0", f"{total - 2000}.0")
        self.log_text.configure(state="disabled")
        if getattr(self, "autoscroll", None) and self.autoscroll.get():
            self.log_text.see("end")

    # ---------- 状态刷新 ----------
    def _apply_snapshot(self, s):
        was = self.attached
        self.attached = bool(s.get("attached"))
        self.poll_gap = 1000 if self.attached else 2000
        if self.attached:
            # 连接提示音：从"未连接"变成"已连接"时响一声。两个细节：
            #   · 界面刚起来时第一次检测到游戏也算（所以初值给 99，见 __init__）；
            #   · 一直连着不响；偶发一次读失败也不误响（要连续 ≥2 次快照未连接才算断过）。
            if getattr(self, "_disconn_streak", 0) >= 2 and self.beep_on_connect:
                core.beep(True, "open")
                print("[界面] 游戏连接提示音")   # 便于自动化断言"只响一次"
            # 不再缀"· 可以操作": 绿灯 + 按钮变亮已经说明能用了, 右边还要放"初次使用"引导条
            self.lamp_conn.set("good", f"已连接 · 战网 {s.get('ver') or '?'}")
            self._conn_tip = LAMP_TIPS["conn_ok"]
            self._disconn_streak = 0
            if not was:
                self.op_state = "idle"   # 新连上(可能游戏重开了)：点击状态不可知，重置
                self.op_time = None
                self._render_op_lamp()
        else:
            self._disconn_streak = min(9, getattr(self, "_disconn_streak", 0) + 1)
            why = s.get("why") or "游戏未运行"
            ver_bad = "版本" in why
            short = "版本不符" if ver_bad else why
            self.lamp_conn.set("bad" if ver_bad else "off", f"未连接 · {short}")
            self._conn_tip = LAMP_TIPS["conn_bad"] % why
        nop, store = s.get("nop"), s.get("store")
        if nop == "nop":
            self.lamps["mem"].set("good", "内存操作 · 已解除")
        elif nop == "orig":
            self.lamps["mem"].set("off", "内存操作 · 未修改")
        elif nop == "??":
            self.lamps["mem"].set("off", "内存操作 · 未连接")
        else:
            self.lamps["mem"].set("bad", "内存操作 · 异常")
        if store == 0:
            self.lamps["opt"].set("good", "选项状态 · 未勾选")
        elif store == 1:
            self.lamps["opt"].set("warn", "选项状态 · 已勾选")
        elif store == -1:
            self.lamps["opt"].set("off", "选项状态 · 未连接")
        else:
            self.lamps["opt"].set("bad", f"选项状态 · 未知{store}")

        keys = s.get("keys") or {}
        reg = set(s.get("reg") or [])
        for name, lbl in getattr(self, "hk_frames", {}).items():
            k = keys.get(name) or "未设置"
            if s.get("paused") and name != "hotkey_master":
                lbl.configure(text=k, fg=T.C["warn"])
            elif name not in reg:
                lbl.configure(text=k, fg=T.C["off"])
            else:
                lbl.configure(text=k, fg=T.C["neon2"])
        if s.get("paused") != getattr(self, "_last_paused", None):
            self._last_paused = s.get("paused")
            self.refresh_hotkeys(paused=bool(s.get("paused")))
        self._sync_buttons(s)

    def _render_op_lamp(self):
        """「选项操作」灯: 文字 + (做过操作时)发生时刻。

        带时间是为了让用户一眼看出"那一下"是刚做的还是上次运行留下的(用户建议)。
        idle 不带时间——那时候本来就没做过操作。
        """
        kind, text, _tip = OP_STATE[self.op_state]
        if self.op_time and self.op_state != "idle":
            text = "%s (%s)" % (text, self.op_time)
        self.lamps["act"].set(kind, text)

    def _sync_buttons(self, s=None):
        s = s or {}
        attached = getattr(self, "attached", False)
        why = s.get("why", "")
        ver_bad = "版本" in why
        usable = attached or ver_bad
        if not self.busy:
            for b in self._all_action_buttons():
                b.set_enabled(usable)

    def _all_action_buttons(self):
        out = [self.btn_uncensor, self.btn_main]
        for n in MORE_ITEMS:
            b = getattr(self, f"btn_{n}", None)
            if b:
                out.append(b)
        return out

    def _set_hint(self, kind, text):
        _require_tk_thread("_set_hint")
        self.hint_lamp.set(kind, "")
        self.hint_lbl.configure(text=text,
                                fg=T.C["text"] if kind in ("good", "warn", "bad")
                                else T.C["text_dim"])

    # ---------- 执行操作 ----------
    def act(self, name):
        if self.busy:
            self._set_hint("warn", HINT["busy"] % self.busy_label)
            return
        fn = {"uncensor": core.uncensor, "uncensor_auto": core.uncensor_auto,
              "click_toggle": core.click_toggle, "restore": core.uncensor_off,
              "restore_auto": core.uncensor_off_auto, "status": core.print_status}.get(name)
        if fn is None:
            return
        if not self.core_ready:
            self._set_hint("warn", HINT["no_hotkey_thread"])
            return
        self.busy = True
        self.busy_label = OP_LABEL.get(name, name)
        for b in self._all_action_buttons():
            b.set_enabled(False)
        self._set_hint("info", BUSY_TEXT.get(name, "正在执行…"))
        # 记下"这次操作开始前"的日志水位线: 失败时只看新写的行, 免得本次会话早先
        # 失败过一次后, 之后任何失败都弹"杀软误报"(见 _maybe_antivirus)
        self._op_logpos = self._log_size()
        threading.Thread(target=self._act_worker, args=(name, fn), daemon=True).start()

    def _act_worker(self, name, fn):
        try:
            code = core.post_ui_call(fn, label=name)
            self.q.put(("op_done", (name, code, None)))
        except Exception as e:
            self.q.put(("op_done", (name, None, str(e))))

    def _toggle_master(self):
        """快捷键总开关：暂停/恢复其他所有快捷键（总开关自身始终有效）"""
        if not self.core_ready:
            self._set_hint("warn", HINT["no_hotkey_thread"])
            return
        if self.busy:
            self._set_hint("warn", HINT["busy"] % self.busy_label)
            return
        self.busy = True
        self.busy_label = OP_LABEL["hotkey_master"]
        self._set_hint("info", BUSY_TEXT["hotkey_master"])

        def worker():
            try:
                core.post_ui_call(lambda: core.set_hotkeys_enabled(not core._hotkeys_enabled),
                                  label="hotkey_master")
                self.q.put(("op_done", ("hotkey_master", 0, None)))
            except Exception as e:
                self.q.put(("op_done", ("hotkey_master", None, str(e))))
        threading.Thread(target=worker, daemon=True).start()

    def _op_done(self, name, code, err):
        self.busy = False
        label = OP_LABEL.get(name, name)
        if err:
            self._set_hint("bad", f"{label} 执行失败：{err}")
            self._open_fold("log")
            self._sync_buttons()
            return
        if name == "hotkey_master" and code == 0:
            # 文案在 ui_text.HINT（带占位符的格式串：% (操作名, 当前键位)）
            self._set_hint("good", HINT["master_on" if core._hotkeys_enabled else "master_off"]
                           % (label, self.effective_key("hotkey_master")))
            self.refresh_hotkeys()
            self._sync_buttons()
            return
        if name == "status" and code is None:
            # print_status() 没有返回值（None）——以前落到"失败"分支，明明成功却报错
            nop = self.lamps["mem"].label.cget("text").split("·")[-1].strip()
            opt = self.lamps["opt"].label.cget("text").split("·")[-1].strip()
            act = OP_STATE[self.op_state][1].split("·")[-1].strip()
            self._set_hint("info", HINT["status_done"] % (nop, opt, act))
            self._open_fold("log")
            self._sync_buttons()
            return
        if name in ("uncensor", "uncensor_auto", "click_toggle", "restore", "restore_auto"):
            self.op_time = time.strftime("%H:%M")     # 灯上显示"最后一次操作是几点做的"
        if code == 0:
            extra = ""
            if name == "uncensor":
                extra = " " + getattr(core, "UNCENSOR_MANUAL_TIP", "")
                self.op_state = "manual"
            elif name == "restore":
                extra = HINT_LIT["restore_manual"]
                self.op_state = "manual"
            elif name in ("uncensor_auto", "click_toggle", "restore_auto"):
                self.op_state = "auto"         # 自动点击并验证命中
            self._set_hint("good", f"{label} 完成。{extra}")
        elif code == 3:
            self.op_state = "skip"
            self._set_hint("warn", label + HINT_LIT["skip_in_game"])
        else:
            if name in ("uncensor_auto", "click_toggle", "restore_auto",
                        "uncensor", "restore"):
                self.op_state = "fail"
            self._set_hint("bad", label + HINT_LIT["fail_see_log"])
            self._open_fold("log")
            self._maybe_antivirus()
        self._render_op_lamp()
        self._sync_buttons()
        self.refresh_hotkeys()

    def _log_size(self):
        """当前日志文件字节数(给"只看本次操作新增的日志"当水位线用)"""
        try:
            return os.path.getsize(core.LOG_PATH)
        except Exception:
            return 0

    def _maybe_antivirus(self):
        """配方的回读校验没过 → 大概率是被杀软拦了, 主动提一句。

        ⚠️ 只看**本次操作新写的那一段**(act() 里记下的水位线): 日志是覆盖式的、整次
        会话的内容都在里面, 若搜全文, 那么本次会话早先失败过一次之后, 之后任何失败
        都会弹出"杀软误报"面板(2026-09-15 审查时改)。
        """
        try:
            with open(core.LOG_PATH, "rb") as f:
                f.seek(getattr(self, "_op_logpos", 0) or 0)
                tail = f.read().decode("utf-8", "replace")
        except Exception:
            return
        if "写入没生效" in tail or "写入未生效" in tail or "内存写入未生效" in tail:
            self.show_antivirus()

    # ---------- 快捷键 ----------
    def effective_key(self, name):
        """当前实际生效的键位（被占用换过备用键就显示备用键）"""
        k = core._KEY_BY_NAME.get(name)
        if k:
            return k
        try:
            return str(core.CFG["hotkeys"].get(name, ""))
        except Exception:
            return ""

    def _alive(self):
        """窗口还在吗——关窗过程中(比如改键弹窗还开着时用户关了主窗口)回调会被打断,
        这时候再去 configure 已销毁的控件就会抛 TclError。"""
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def refresh_hotkeys(self, paused=None):
        """刷新所有键位显示。

        总开关暂停时：各操作按钮下面那行显示"已关闭"（灰），总开关自己显示"已暂停"——
        以前暂停后界面上完全看不出来，用户只能靠"按了没反应"猜（实测反馈）。
        """
        _require_tk_thread("refresh_hotkeys")
        if not self._alive():
            return
        if paused is None:
            paused = not core._hotkeys_enabled
        for name, lbl in getattr(self, "hk_frames", {}).items():
            lbl.configure(text=self.effective_key(name),
                          fg=(T.C["warn"] if paused and name != "hotkey_master" else T.C["neon2"]))
        for name in OP_LABEL:
            b = getattr(self, f"btn_{name}", None)
            if b is None:
                continue
            if name == "hotkey_master":
                # 总开关本身体现"启用中/已暂停": 第二行文字 + **右上角小灯 + 键位行配色**
                # (用户反馈: 只靠文字看不出来, 想要颜色/小灯这种一眼能看出的变化)
                b.set_text(OP_LABEL[name],
                           MASTER_KEY_FMT % (self.effective_key(name),
                                             MASTER_STATE["off" if paused else "on"]),
                           key_color=(T.C["off"] if paused else T.C["neon2"]),
                           lamp=("off" if paused else "good"))
            elif paused:
                b.set_text(OP_LABEL[name], BTN_PAUSED_KEY, key_dim=True)
            else:
                b.set_text(OP_LABEL[name], self.effective_key(name))

    def edit_hotkey(self, name):
        if self.busy:
            self._set_hint("warn", HINT_LIT["hk_busy"])
            return
        prev = core._hotkeys_enabled
        # 捕获期间必须暂停其他快捷键，否则按 F9 会先把 F9 的功能执行一遍。
        # 这里**同步**设置（几毫秒的事）：以前用线程异步调用，理论上存在"恢复先到、
        # 暂停后到"的竞态，会让用户关掉弹窗后发现所有快捷键变成"已暂停"（实测反馈）。
        self._set_hotkeys_enabled(False, silent=True)
        self._set_hint("info", NOTICE["hk_pausing"])      # 说明为什么键位变黄了
        dlg = HotkeyDialog(self, name)
        dlg.wait_window()
        newkey = dlg.result
        self._set_hotkeys_enabled(prev, silent=True)
        if not newkey:
            return
        self._save_hotkey(name, newkey)

    def _set_hotkeys_enabled(self, enabled, silent=False):
        """同步设置"其他快捷键"的开关（幂等）；失败只记日志，不打断流程。

        silent=True 时临时把 CFG.beep 关掉——核心的 set_hotkeys_enabled 自带提示音，
        而改键这类界面动作不该出声（用户要求：只有点"确定"才响）。
        """
        try:
            if silent:
                was = core.CFG.get("beep", True)
                core.CFG["beep"] = False
                try:
                    core.post_ui_call(lambda: core.set_hotkeys_enabled(enabled), timeout=8)
                finally:
                    core.CFG["beep"] = was
            else:
                core.post_ui_call(lambda: core.set_hotkeys_enabled(enabled), timeout=8)
        except Exception as e:
            self.q.put(("log", f"[界面] 设置快捷键开关失败({enabled}): {e}"))
        self.refresh_hotkeys()

    def _save_hotkey(self, name, key):
        try:
            cfg = json.load(open(core.CONFIG_PATH, encoding="utf-8-sig"))
        except Exception:
            cfg = {}
        cfg.setdefault("hotkeys", {})[name] = key
        try:
            core._write_config(cfg)
        except Exception as e:
            self._set_hint("bad", f"写 config.json 失败：{e}")
            return
        core.CFG["hotkeys"][name] = key           # 立即生效（不重启）
        self._set_hint("info", f"{OP_LABEL.get(name, name)} 的快捷键已改为 {key}，正在重新注册…")
        if os.environ.get("SC2_UNCENSOR_UI_NO_HOTKEYS"):
            print("[界面] (测试模式: 只写配置, 跳过重新注册, 免得抢占用户按键)")   # 自动化用
            self.refresh_hotkeys()
            self._set_hint("good", f"{OP_LABEL.get(name, name)} 的快捷键已写入配置：{key}")
            return
        threading.Thread(target=self._reload_worker, args=(name, key), daemon=True).start()

    def _reload_worker(self, name, key):
        try:
            ok, failed = core.post_ui_call(core.reload_hotkeys, label="reload_hotkeys", timeout=8)
            self.q.put(("hk_done", (ok, failed, name, key)))
        except Exception as e:
            self.q.put(("hk_done", ([], [(name, key)], name, key, str(e))))

    def _hk_done(self, ok, failed, name, key, err=None):
        self.refresh_hotkeys()
        lab = OP_LABEL.get(name, name)
        real = next((k for n, k in ok if n == name), None)
        bad = next((k for n, k in failed if n == name), None)
        if err:
            self._set_hint("bad", HINT_LIT["hk_reload_err"] % err)
            core.beep(False, "fail")
        elif bad:
            self._set_hint("bad", HINT_LIT["hk_taken"] % (lab, key))
            core.beep(False, "fail")
        elif real and real != key:
            self._set_hint("warn", HINT_LIT["hk_backup"] % (lab, key, real))
            core.beep(True, "open")
        else:
            self._set_hint("good", HINT_LIT["hk_ok"] % (lab, real or key))
            core.beep(True, "open")          # 只有"确定"这条路会响（用户要求）

    # ---------- 设置项 ----------
    def _toggle_cfg(self, key):
        val = bool(self.set_vars[key].get())
        try:
            cfg = json.load(open(core.CONFIG_PATH, encoding="utf-8-sig"))
        except Exception:
            cfg = {}
        cfg[key] = val
        try:
            core._write_config(cfg)
        except Exception as e:
            self._set_hint("bad", f"写 config.json 失败：{e}")
            return
        core.CFG[key] = val                     # 核心是按需读 CFG 的，改完即时生效
        note = {"log": "（日志开关要重启工具才生效）", "debug": "（下次识别时生效）"}.get(key, "")
        self._set_hint("good", f"已{'开启' if val else '关闭'}该项设置{note}")

    def open_data_dir(self):
        try:
            os.startfile(DATA_DIR)
        except Exception as e:
            self._set_hint("bad", f"打开目录失败：{e}")

    def edit_config(self):
        try:
            os.startfile(core.CONFIG_PATH)
        except Exception as e:
            self._set_hint("bad", f"打开 config.json 失败：{e}")

    def open_log_file(self):
        try:
            os.startfile(core.LOG_PATH)
        except Exception as e:
            self._set_hint("bad", f"打开日志失败：{e}")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def copy_log(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))
            self._set_hint("good", HINT_LIT["clip_done"])
        except Exception:
            pass

    def is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def restart_as_admin(self):
        """设置里的「管理员重启」：直接 runas 提权拉起新实例，然后自己退出。

        不经过 PowerShell 助手：那样 UAC 要等助手冷启动 + 等本进程退出，实测 3 秒左右
        才弹窗（用户反馈"有点慢"）。现在直接 ShellExecuteW(runas) → UAC 秒弹；
        "等旧实例退出"由带 --restart 的新实例自己做（见 _wait_other_instances）。
        """
        if self.is_admin():
            self._set_hint("good", HINT_LIT["already_admin"])
            return
        if self.busy:
            self._set_hint("warn", HINT_LIT["restart_busy"])
            return
        if not self._elevate():
            self._set_hint("warn", HINT_LIT["elevate_cancelled"])
            return
        self.quit_app()

    def _elevate(self):
        """请求以管理员身份启动一个新实例（返回是否发出）。失败不抛异常。"""
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, elevate_params(), DATA_DIR, 1)
            print(f"[界面] 已请求提权启动新实例: {sys.executable} {elevate_params()}")
            return rc > 32
        except Exception as e:
            self._set_hint("bad", f"提权启动失败：{e}")
            return False

    def _wait_other_instances(self, timeout=8.0):
        """提权/重启拉起的实例：先等上一个实例真的退出，再启动核心。

        为什么必须等：单实例互斥锁要到旧进程退出才释放；抢跑会被核心判成"已在运行"。
        而核心的 _single_instance_check() 一旦判过"已存在"，本进程内就再也不会成功
        （它没关掉自己拿到的句柄，见 docs/UI说明.md §10.4），所以**必须等干净再启动核心**。
        """
        end = time.time() + timeout
        told = False
        while time.time() < end:
            others = other_instances()
            if not others:
                print("[界面] 上一个实例已退出, 继续启动")
                self.q.put(("hint", ("info", HINT["ready"])))
                return True
            if not told:
                told = True
                names = "、".join(f"{n}(pid {p})" for p, n in others)
                print(f"[界面] 等待上一个实例退出: {names}")
                self.q.put(("hint", ("warn", HINT_LIT["wait_other"])))
            time.sleep(0.25)
        print("[界面] 等待上一个实例超时, 仍尝试启动核心")
        return False

    # ---------- 弹窗 ----------
    def _notice(self, title, body, buttons, **kw):
        _require_tk_thread("_notice")
        dlg = Notice(self.root, title, body, buttons, scale=self.s, **kw)
        self._last_notice = dlg
        return dlg

    def show_antivirus(self, first=False):
        body = ANTIVIRUS_TIP
        try:
            body += f"\n\n本工具目录：{DATA_DIR}"
        except Exception:
            pass

        def add_defender():
            cmd = f"Add-MpPreference -ExclusionPath '{DATA_DIR}'"
            self._notice(NOTICE["defender_title"], NOTICE["defender_body"] % cmd,
                         [(NOTICE["defender_run"], lambda: self._run_ps(cmd), True),
                          (NOTICE["cancel"], lambda: None, False)],
                         width=560, height=280)
        # 高度按内容收一收(原来 470 富余太多, 底下空一大片): 正文 11 行 + 标题栏 + 按钮行
        # 大约 355, 取 370。内容真比这高时 finish() 会用请求高度兜底(文本框还能滚)。
        dlg = self._notice(NOTICE["antivirus_title"], "",
                           [(NOTICE["antivirus_add"], add_defender, True),
                            (NOTICE["antivirus_open"], self.open_data_dir, False),
                            (NOTICE["ok"], lambda: None, False)], width=660, height=370)
        self._fill_blocks(dlg.txt, _reflow_doc(body), 660)
        dlg.fit_content()
        return dlg

    def _run_ps(self, cmd):
        if not self.is_admin():
            self._set_hint("warn", HINT_LIT["defender_need_admin"])
            self.restart_as_admin()
            return
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "open", "powershell",
                f'-NoProfile -Command "{cmd}"', DATA_DIR, 0)
            self._set_hint("good", HINT_LIT["defender_ok"])
        except Exception as e:
            self._set_hint("bad", f"执行失败：{e}")

    def show_terms(self):
        """名词说明：内容照 app/sc2_uncensor.py 与 app/README.txt（作者核对过的版本）。

        条目名加粗、正文左缩进、按弹窗宽度重新折行(原来直接用源文的换行, 宽一点的弹窗里
        会"几个字就换行", 用户实测反馈)。
        """
        blocks = []
        for name, val in TERMS.items():
            if blocks:
                blocks.append(("", ""))
            blocks.append((name, "head"))
            # 定义统一左缩进(与使用说明里章节正文同一层次, 不然条目名与正文同一竖线、层次不分)
            blocks += [(t, "body" if k in ("plain", "body") else k)
                       for t, k in _reflow_doc(val)]
        dlg = self._notice(NOTICE["terms_title"], "",
                           [(NOTICE["close"], lambda: None, True)],
                           width=660, height=520)
        self._fill_blocks(dlg.txt, blocks, 660)
        dlg.fit_content()
        return dlg

    def show_help(self):
        """使用说明：正文直接引用 py 里的说明(不复制一份)。

        但**不能原样塞进去**：py 那份是按控制台 80 列硬换行的，弹窗里会句子中间断行。
        所以先用 _reflow_doc 重新分段、再按"缩进 + 标题加粗"排一遍(见 _fill_help_text)，
        观感向 bat 里的排版看齐。
        """
        # 宽度 660(原 620): 620 时正文一行约 38 个中文字, 少数几行会多出"）加一个符号"
        # 就换行(用户实测反馈); 660 一行约 40.7 字, 这类零头行基本都收回去了
        dlg = self._notice(NOTICE["help_title"], "",
                           [(NOTICE["open_readme"], self.open_readme, True),
                            (NOTICE["close"], lambda: None, False)], width=660, height=520)
        self._fill_help_text(dlg.txt, 660)
        dlg.fit_content()
        return dlg

    def _fill_blocks(self, txt, blocks, width):
        """把 [(文本, 类型)] 按"bat 观感"画进弹窗正文: 缩进段落挂 lmargin, 标题加粗。

        所有文字类弹窗(使用说明/名词说明/免责声明/杀软白名单)都走这里 —— 它们的源文都是
        按控制台排版写的, 换行位置与弹窗宽度不一致, 必须先 _reflow_doc 再画(用户实测反馈:
        bat 里好看, 弹窗里"几个字就换行"/条目混在一起)。
        """
        s = self.s
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        # 关掉 Tk 自己的折行(wrap="none")以后, Text 的"请求宽度"就没用了 —— 留着它
        # (默认 80 字符)反而会和 finish() 给的 geometry 抢, 让**弹窗宽度成了一次巧合**
        # (实测在这个字体/缩放下恰好等于 825，换字体/DPI 就可能被它撑宽)。折行宽度由
        # _text_avail 按弹窗宽自己算，所以把请求宽度收到最小最稳。
        txt.configure(width=1)
        ind = int(T.font(10, scale=s).measure("【"))
        txt.tag_configure("ind", lmargin1=ind, lmargin2=ind)
        txt.tag_configure("head", font=T.font(10, True, s), spacing1=int(4 * s))
        # 自己按像素折行(带中文禁则), 然后关掉 Tk 的自动折行 —— 这样断点位置我们说了算。
        avail, hang = _text_avail(txt, width, s)
        body_f, head_f = T.font(10, scale=s), T.font(10, True, s)
        for i, (text_, kind) in enumerate(blocks):
            if i:                                   # 块之间只换一行; 段落间距由源文里的空行块给
                txt.insert("end", chr(10))          # (以前每块前都空一行 → 整篇忽松忽紧, 层次乱)
            if not text_:
                continue
            f = head_f if kind == "head" else body_f
            lw = avail - (ind if kind == "body" else 0)
            txt.insert("end", _wrap_cjk(text_, f.measure, lw, hang),
                       {"body": "ind", "head": "head"}.get(kind, ()))
        txt.configure(wrap="none", state="disabled")


    def _fill_help_text(self, txt, width):
        """把使用说明正文按"缩进 + 标题加粗"重画一遍（照 bat 里的排版观感: 正文右移两格）。

        文本内容与 _reflow_doc 的结果一致, 只是给缩进段落挂 lmargin、给【标题】挂粗体。
        """
        blocks = []
        notice_txt = chr(10).join(getattr(core, "NOTICE", []) or [])
        head = ("%s v%s" % (APP_TITLE, core.VERSION)) + chr(10) + ("作者: %s" % _author_full())
        blocks.append((head, "plain"))
        blocks.append(("", ""))
        blocks += _reflow_doc(notice_txt)
        blocks.append(("", ""))
        doc = _reflow_doc(core.__doc__ or "")
        if doc and doc[0][0].startswith("sc2_uncensor.py"):
            doc = doc[1:]              # 弹窗顶上已经有"工具名+版本+作者", 这行是重复的
        blocks += doc
        blocks.append(("", ""))
        blocks.append(("手动模式提示：", "head"))
        blocks.append((getattr(core, "UNCENSOR_MANUAL_TIP", ""), "body"))
        blocks.append(("", ""))
        blocks.append(("完整说明与常见问题见工具目录下的 README.txt。", "plain"))
        self._fill_blocks(txt, blocks, width)

    def open_readme(self):
        for p in (os.path.join(DATA_DIR, "README.txt"), os.path.join(_HERE, "README.txt")):
            if os.path.exists(p):
                try:
                    os.startfile(p)
                    return
                except Exception:
                    pass
        self._set_hint("warn", HINT_LIT["readme_missing"])

    def show_disclaimer(self):
        """免责声明弹窗(常驻底部按钮用): 正文同样重排后再画, 别直接甩源文的换行。"""
        # 宽度 660: 与另外三个文字弹窗统一(§9.2), 同时消掉"· 内存读写工具…请看下面
        # "杀软白名单"的说明；"那条里最后一行的"明；"两个字(640 宽差 21px 放不下,
        # 660 宽正好一行 —— 用户实测反馈"只有两个字符了，能不能放到上一行")
        # 高度同样收一收(460 → 370): 加宽后正文只要 9 行, 再留 460 底下就空一大块
        dlg = self._notice("使用须知与免责声明", "", [("知道了", lambda: None, True)],
                           width=660, height=370)
        self._fill_blocks(dlg.txt, _reflow_doc(DISCLAIMER), 660)
        dlg.fit_content()
        return dlg

    def show_notice(self, title, body, quit_after=False):
        _require_tk_thread("show_notice")
        dlg = self._notice(title, body,
                           [(NOTICE["quit_tool"] if quit_after else NOTICE["ok"],
                             self.quit_app if quit_after else (lambda: None), True)],
                           width=540, height=340)
        try:
            dlg.attributes("-topmost", True)      # 桌面窗口多时别被盖住(看着像没反应)
        except Exception:
            pass
        return dlg

    def _on_esc(self):
        """Esc: 窗口焦点丢过就把它找回来（无边框窗口没原生快捷键）"""
        if self.root.focus_get() is None:
            self.root.focus_force()

    # ---------- 生命周期 ----------
    def _toggle_beep_on_connect(self):
        on = bool(self.ui_vars["connect_beep"].get())
        self.state["connect_beep"] = on
        save_state(self.state)
        self.beep_on_connect = on
        self._set_hint("info", HINT["waiting_game"] if on else HINT["wait_off"])

    def quit_app(self):
        if self.busy:
            self._set_hint("warn", HINT_LIT["quit_busy"])
            return
        try:
            rect = wt.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            self.state["pos"] = [rect.left, rect.top]      # Win32 物理坐标
            save_state(self.state)
        except Exception:
            pass
        self._destroy_root()

    def run(self):
        self.root.mainloop()


def main():
    # 提权预检放在最前面（不 import 核心，只用 json 读 config.json）：这样双击 exe/bat 后
    # UAC 很快就弹出来，而不是等核心把 pymem/PIL/winocr 全加载完（那要 1~2 秒）。
    if precheck_elevate():
        return 0
    app = App()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
