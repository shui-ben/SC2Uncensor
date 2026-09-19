# -*- coding: utf-8 -*-
"""
sc2_uncensor.py — 星际争霸2 国服反和谐工具 v1.6
=================================================================
【功能】
  把国服客户端被隐藏的"减少暴力表现"选项找回并自动切换,
  恢复国际服的完整死亡特效。内存级、不改游戏文件、游戏本次运行内有效。
  注: 少部分战役场景模型跟随简中语音包依然和谐。
  不建议在对战/天梯等PVP模式中使用本工具，
  PVE没什么审查 各种修改刷级很常见，PVP审查力度更大 没有经过测试 可能存在风险。
  仅支持国服 Windows 64 位 V5.0.15/V5.0.16 版游戏，
  会自动核对版本, 不符则停止 防止内存写坏，
  但由于星际2deadgame,小补丁更新可能不会改变内存地址，
  所以可在 config.json设 "check_version": false 跳过核对自行尝试(风险自负)。


【操作方式】
  快捷键操作。切勿在对局内使用，在主界面等地方使用。
  对局内"减少暴力表现"选项锁定无法操作。

  1手动模式反和谐(uncensor, 默认 F8): 需要部分手动操作的反和谐。
  程序完成 内存部分反和谐(NOP+暴露), 
  然后需要用户手动去选项>画面设置 里 勾选→取消勾选 一次(手动操作后反和谐生效)。

  2自动模式反和谐(uncensor_auto, 默认 F9): 基于ocr自动操作菜单的全自动反和谐。
  程序完成 内存部分反和谐+OCR识别 自动打开画面设置操作 并返回原界面。
  全程约2秒 且无需手动操作。

  3快捷键总开关(hotkey_master, 默认 Ctrl+Alt+1): 按一下暂停其他快捷键(解除按键占用), 
  再按一下恢复。总开关自身始终有效。

  4自动切换和谐状态(click_toggle, 默认 F11): 基于ocr自动操作菜单切换和谐与否。
  程序自动打开画面设置切换"减少暴力表现"选项，并返回原界面。
  全程约2秒 且无需手动操作。

【快捷键自定义】
  界面版: 点功能后面的"修改"按下新键即可, 改完立即生效(不用重启);
  手工编辑 config.json 的 hotkeys 段则需重启工具。
  可用键: F1~F12 / 字母 / 主键盘数字 / 小键盘 / 编辑键区与方向键 / 符号键 /
  鼠标中键与侧键(MouseMiddle Mouse4 Mouse5) / Pause ScrollLock 等,
  再叠加 Ctrl Alt Shift Win 可组成组合键。完整键名见 config.json 的 _说明.hotkeys。

【其他】
  不联网、不改游戏文件。日志与提示音开关在 config.json("log"/"beep")。
  若 SC2 以管理员启动，则该工具也需以管理员启动, 
  并且可在 config.json 设 "admin": true 让启动器自动提权至管理员
  (界面版双击 exe 就弹 UAC; 源码版双击 bat 弹)。
  自动模式/自动切换和谐状态 会全程把星际拉到前台操作。 
  检测到在对局内时 自动点击等不生效(内存操作生效, 实测无害)。

  版本核对说明: 启动时自动核对游戏版本(exe 内嵌版本号, 实测支持V5.0.15/V5.0.16), 
  版本不符会拒绝内存操作(防止写坏)。星际2已进入维护期, 
  小补丁的内存偏移通常不变，被版本核对拦住时, 可在 config.json
  设 "check_version": false 跳过核对自行尝试(风险自负); 
  新版本的内存偏移也可在 offsets.dat 添加条目适配(方法见 README.txt【进阶: 游
  戏更新后自己适配偏移】; 仓库里 docs/偏移维护指南.md 有更详细的流程)。
"""
import sys
import os
import json
import time
import ctypes
import ctypes.wintypes as wt
import winsound
import threading
import queue
import atexit
from datetime import datetime

import psutil
import pymem
import pymem.process
from PIL import ImageGrab

__author__ = "水本、glm5.3flash、deepseek4.1flash"   # 显示在启动横幅; 打包版本资源也会用到

# 数据文件(配置/偏移/日志/OCR脚本)的查找根目录。
# SC2_UNCENSOR_DIR 可覆盖它: PyInstaller --onedir 打包后 __file__ 指向 _internal
# 子目录, 用户改不到 config.json / offsets.dat; 界面版会在 import 之前把它设成
# exe 同目录(或任何可写目录), 让用户双击记事本就能改配置。
# 优先级: SC2_UNCENSOR_LOG(仅日志) > SC2_UNCENSOR_DIR > py 文件所在目录
BASE_DIR = (os.environ.get("SC2_UNCENSOR_DIR")
            or os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
VERSION = "1.6"

# ---------------- 补丁位 (模块偏移 = Ghidra地址-0x140000000, 5.0.16b 实测) ----------
NOP_SITES = [
    (0x11D9F0, bytes.fromhex("742A"), "CHN条件1"),
    (0x11D9F9, bytes.fromhex("7421"), "CHN条件2"),
    (0x11DA02, bytes.fromhex("7518"), "CHN条件3"),
]
NOP_BYTE = b"\x90\x90"
OFF_STORE = 0x3E2E381              # 存储位(CVariableT<bool>): 0=未勾选(完整死亡特效)
OFF_R = 0x3E2B5A8                  # R 三态: 0=和谐 1=完整 2=CHN/ROK强制
# 内置偏移的出厂副本(offsets.dat 不命中时重置用, 防上次会话的覆盖残留)
_BUILTIN_NOP_SITES = list(NOP_SITES)
_BUILTIN_OFF_STORE, _BUILTIN_OFF_R = OFF_STORE, OFF_R
SUPPORTED_BUILDS = ("5.0.15", "5.0.16")   # 内置偏移实测可用: 5.0.15.32043(本机验证)与 5.0.16.x。
# 内置偏移是写死的(不是特征扫描), 实测在 5.0.15/5.0.16 上有效。其他版本可在
# offsets.dat(与 py 同目录, JSON 内容 .dat 后缀防误改)按格式加条目提供偏移;
# 都没有且 check_version=true 时拒绝内存操作, 防止把别的版本写坏。
# 适配方法见 README.txt(随包分发)与仓库 docs/偏移维护指南.md;
# 自动重定位思路见 docs/特征重定位思路.md。
_BUILTIN_EXE_VER = "5.0.15.32043"   # 本机完整实测验证的 exe 内嵌版本号
_BUILTIN_DISPLAY = "5.0.16.97579"   # 该版本在战网上显示的版本号(与 exe 内嵌版本不同步)

# ---------------- 配置 ----------------
DEFAULT_CONFIG = {
    "_说明": {
        "hotkeys": "快捷键设置, 支持单键与组合键(如 Ctrl+Alt+1)。改完生效: 界面版在\"快捷键设置\"里改完立即生效, 手工编辑本文件则需重启工具。注意: F1~F12 全部被星际2自带功能占用(闲兵/部队/镜头/成就/菜单/聊天/帮助), 默认改用 F8/F9/F11(主界面无功能的键)与 Ctrl+Alt+1。可用键名: 字母/主键盘数字1~0 / 小键盘 Num0~Num9 / NumAdd NumSub NumMul NumDiv NumDec / MouseMiddle Mouse4 Mouse5(鼠标中键侧键, 会被全局接管) / Pause ScrollLock / Insert Delete Home End PageUp PageDown / 方向键 Up Down Left Right / Tab Space Enter CapsLock Backspace / 符号键 ` - = [ ] \\ ; ' , . /。修饰键 Ctrl Alt Shift Win 可组合。",
        "hotkeys.uncensor": "手动模式反和谐(F8): 需要部分手动操作的反和谐。程序完成内存部分反和谐(NOP+暴露), 然后需要用户手动去 选项>画面设置 里 勾选→取消勾选 一次(手动操作后反和谐生效)。",
        "hotkeys.uncensor_auto": "自动模式反和谐(F9): 基于OCR自动操作菜单的全自动反和谐。程序完成内存部分反和谐+自动打开画面设置切换选项并返回原界面, 全程约2秒, 无需手动操作。",
        "hotkeys.click_toggle": "自动切换和谐状态(F11): 自动打开画面设置切换\"减少暴力表现\"选项, 并返回原界面, 全程约2秒, 无需手动操作(再按一次切回)。",
        "hotkeys.hotkey_master": "快捷键总开关(Ctrl+Alt+1): 按一下暂停其他所有快捷键(按键返还系统, 不影响其他软件使用), 再按一下恢复。总开关自身始终有效。",
        "hotkeys.status": "显示当前状态(Ctrl+Alt+2): 显示 NOP/存储位/R 当前值(打在日志里), 纯查看不修改",
        "hotkeys.restore": "手动关闭反和谐修改(Ctrl+Alt+3): 与手动反和谐相反。程序把内存部分恢复原状(NOP恢复+和谐位), 然后需要用户手动去 选项>画面设置 里 点一下\"减少暴力表现\"(点完恢复初始态)。",
        "hotkeys.restore_auto": "自动关闭反和谐修改(Ctrl+Alt+4): 与全自动反和谐相反。程序把内存部分恢复原状+自动打开画面设置点一下\"减少暴力表现\"并返回原界面, 全程约2秒, 无需手动操作。",
        "beep": "提示音开关(true=操作时有蜂鸣反馈)",
        "log": "日志开关(true=每次运行写 sc2_uncensor.log, 覆盖式只保留最新一次)",
        "verify_click_ms": "自动点击后等待存储位翻转的最长毫秒数(轮询, 通常几十毫秒就翻转并立刻继续; 超时才判未命中)。设成 0 或比 50 还小没有意义: 程序按 50ms 兜底——不加这个下限的话, 每次点击都会因为等不到翻转而被判成未命中",
        "forward_keys": "按键转发(快捷键拦截补偿): 全局快捷键是独占的, 其他程序无法使用, 开启后按快捷键时异步把按键转发给前台的游戏(对局内 F8 镜头/F9 成就/F11 聊天等功能照常)。仅支持无修饰键的单键(字母/数字/F键/小键盘/编辑键区/符号键); 组合键与鼠标键不转发。默认键(F8/F9/F11, 主界面无功能)只在对局内转发; 改成非默认键后只要星际在前台就转发(不分对局内外)。",
        "debug": "调试开关(true=把送OCR的图/像素扫描图存到 app/debug_*.png, 识别失败时发回分析)",
        "admin": "启动时自动申请管理员权限(true=启动工具时就弹UAC提权(界面版双击exe/源码版双击bat), false=不弹)。默认false: SC2未提权时非管理员即可正常工作; 若对方以管理员启动SC2才需要true。以后UI里就是'管理员启动'勾选项",
        "check_version": "游戏版本核对(true=挂载时核对, exe版本不在实测列表(V5.0.15/V5.0.16)则拒绝内存操作防写坏; false=跳过核对直接用——小补丁偏移通常不变, 可自行尝试, 风险自负)",
        "screenshot": "截图方式: auto=自动(默认, 桌面截屏黑屏时自动改用按窗口抓) / bitblt=只用桌面截屏(最快) / printwindow=只用按窗口抓。独占全屏下若提示定位失败, 可试 printwindow(与绝区零一条龙同款 API)",
    },
    "hotkeys": {
        "hotkey_master": "Ctrl+Alt+1",  # 快捷键总开关: 暂停/恢复其他所有快捷键
        "uncensor": "F8",            # 手动模式: 内存部分, 然后自己去选项里点一次
        "uncensor_auto": "F9",   # 全自动: 内存部分 + 自动开对话框 + OCR点击两次
        "click_toggle": "F11",   # 自动切换和谐状态
        "status": "Ctrl+Alt+2",
        "restore": "Ctrl+Alt+3",       # 关闭反和谐·手动: 内存恢复, 手动点一次
        "restore_auto": "Ctrl+Alt+4",  # 关闭反和谐·自动: 内存恢复+自动点+回主界面
    },
    "beep": True,
    "log": True,
    "forward_keys": True,
    "verify_click_ms": 300,
    "debug": False,
    "admin": False,
    "check_version": True,
    "screenshot": "auto",
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            # utf-8-sig: 记事本另存"UTF-8(带BOM)"也能读; 否则解析失败会把用户
            # 的配置(快捷键/admin等)直接覆盖成默认值
            cfg = json.load(open(CONFIG_PATH, encoding="utf-8-sig"))
            for k, v in DEFAULT_CONFIG.items():
                if k == "hotkeys":
                    # 快捷键逐键补缺: 新增键名自动获得默认值(旧配置文件也能升级)
                    hk = cfg.setdefault("hotkeys", {})
                    for hk_name, hk_def in v.items():
                        hk.setdefault(hk_name, hk_def)
                else:
                    cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            print(f"[警告] config.json 解析失败({e}), 用默认配置")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    _write_config(cfg)
    return cfg


def _write_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CFG = load_config()

# ---------------- 日志(可选; 每次运行覆盖, 只保留最新一次) ----------------
# 环境变量可改日志位置: 测试脚本必须用它指向临时文件, 否则会把正在运行的工具
# 日志覆盖掉(2026-09-14 踩过: 跑单测把正在跑的工具的实时日志清空了)
LOG_PATH = os.environ.get("SC2_UNCENSOR_LOG") or os.path.join(BASE_DIR, "sc2_uncensor.log")
_LOG_F = None


class Tee:
    def __init__(self, stream, path):
        self.stream = stream
        self.f = open(path, "w", encoding="utf-8")   # 覆盖式: 只留最新一次
        self._buf = ""
        self._lock = threading.Lock()                # 看门狗线程也会写日志

    def write(self, s):
        self.stream.write(s)                         # 显示: 异步队列, 不会卡
        self.stream.flush()
        try:
            # print() 是分次 write(内容/分隔符/换行) 调用, 逐次落盘会让每条日志
            # 后面多出空行; 这里按整行缓冲, 攒够一行才写(日志与屏幕逐行对应)
            with self._lock:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    self.f.write(line + "\n")
                self.f.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.stream.flush()
            with self._lock:
                if self._buf:
                    self.f.write(self._buf)
                    self._buf = ""
                self.f.flush()
        except Exception:
            pass


class _ConsoleDrain:
    """把控制台输出交给后台线程写, 主流程永不阻塞。

    【2026-09-14 实机事故根因】Windows 控制台只要有人在窗口里用鼠标划选文字
    (QuickEdit/标记模式), conhost 就不再读取该进程的输出, 进程的 WriteConsoleW
    会**永久阻塞**——实测把整个快捷键消息循环拖死: 快捷键仍被本进程注册着(按键被
    系统吞掉、游戏也收不到)、进程存活但 0 CPU、日志从此不再增加, 表现为"按键
    全无反应"。这里只投递不落笔; 控制台真卡住时最多丢显示行(文件日志仍完整)。
    注: 不去关 QuickEdit(那会连带禁掉滚轮滚动), 划选只会让显示暂停, 按 Esc 恢复。"""
    MAXQ = 2000
    STUCK_SEC = 1.0                   # 单次写超过它就认为"控制台卡住了"(正常 <1ms)

    def __init__(self, real):
        self.real = real
        self.q = queue.Queue(maxsize=self.MAXQ)
        self.dropped = 0
        self.stuck = False
        self._pending = 0                 # 已投递但还没写完的片段数
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        atexit.register(self.drain_at_exit)

    def _pump(self):
        while True:
            s = self.q.get()
            t0 = time.time()
            try:
                self.real.write(s)
                self.real.flush()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._pending -= 1
                if time.time() - t0 > self.STUCK_SEC:
                    self.stuck = True     # 控制台被划选/卡住: 显示会后滞, 工具不受影响

    def write(self, s):
        with self._lock:
            self._pending += 1
        try:
            self.q.put_nowait(s)
        except queue.Full:
            with self._lock:
                self._pending -= 1
            self.dropped += 1             # 只丢"显示", 文件日志照写
        return len(s)

    def drain_at_exit(self, timeout=1.5):
        """退出前尽量把积压的显示行吐完(最多等 timeout 秒; 控制台若被划选卡住就
        放弃等待——绝不为了显示把进程卡在退出上)"""
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if self._pending <= 0:
                    return
            time.sleep(0.02)

    def flush(self):
        pass                              # 真刷新由后台线程做

    def isatty(self):
        return False


class _NullOut:
    """stdout 为 None 时(pythonw)给它一个能吃的对象"""

    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


# 顺序要紧: Tee 在外、drain 在内。Tee 先把显示部分丢给 drain(队列, 不阻塞), 再同步写
# 文件——控制台被划选卡住时, 文件日志照样一行不落(它是我们排查的依据)。
#
# 但**异步只在真控制台才开**(2026-09-15 踩到): 队列是后台线程写的, 进程退出时如果
# 后台线程没来得及跑完, 最后几行就会丢(打包闸按"输出末尾要有'通过'哨兵行"判定时,
# 偶发丢尾被误判成测试失败)。而"控制台窗口被鼠标划选导致写日志卡死"这个风险**只在
# 真控制台**存在; 输出被重定向到管道/文件时同步写既完整又没这个风险。
_raw_out = sys.stdout
if _raw_out is not None:
    # cp936(cmd 默认代码页) 编不出的字符(比如 ✓ ⚠)会让 print 直接抛 UnicodeEncodeError ——
    # 用户把输出重定向到文件、或从 cmd 跑打包脚本时就会踩到(2026-09-15 实测)。
    # 统一改成"编不出就用 ? 代替", 从此任何字符都不会再把工具/脚本打崩。
    try:
        _raw_out.reconfigure(errors="replace")
    except Exception:
        pass
_real_out = _raw_out if _raw_out is not None else _NullOut()
if _real_out.isatty():
    _real_out = _ConsoleDrain(_real_out)
if CFG.get("log", True):
    sys.stdout = Tee(_real_out, LOG_PATH)
else:
    sys.stdout = _real_out
_author_tag = f" | 作者: {__author__}" if __author__.strip("（）() ") else ""
print(f"\n=== 星际争霸2 国服反和谐工具 v{VERSION}{_author_tag} | {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
NOTICE = [
    "※ 请仔细阅读使用须知及免责声明",
    "※ 本工具仅供学习交流及个人使用，且完全免费。使用即表示您确认：",
    "※ （一）您将完全自行承担使用风险",
    "※ （二）概不负责任何后果（不限于账号封停等）",
    "※ （三）您不会出售本工具或以其他形式借此工具牟利",
    "※ （四）不建议在对战/天梯等PVP模式中使用本工具",
    "※ 仅支持国服 Windows 64 位 V5.0.15/V5.0.16 版游戏(自动核对版本, 不符则停止内存操作)",
]
for _n in NOTICE:
    print(_n)
print("※ 提示: 在控制台窗口里划选文字会让窗口显示暂停(工具运行不受影响, 按 Esc 恢复); "
      "完整日志随时可看 app/sc2_uncensor.log")

# ---------------- 快捷键 ----------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
WM_APP_FORWARD = 0x8000 + 1   # 转发请求: 主线程注销快捷键→带扫描码转发→重注册
KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_SCANCODE = 0x0002, 0x0001, 0x0008
VK_MAP = {f"F{i}": 0x70 + i - 1 for i in range(1, 13)}
# 扩展键名(大小写不敏感): 小键盘/鼠标侧键/编辑键区/符号键
VK_MAP.update({
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63, "NUM4": 0x64,
    "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67, "NUM8": 0x68, "NUM9": 0x69,
    "NUMADD": 0x6B, "NUMSUB": 0x6D, "NUMMUL": 0x6A, "NUMDIV": 0x6F, "NUMDEC": 0x6E,
    "MOUSEMIDDLE": 0x04, "MOUSE4": 0x05, "MOUSE5": 0x06,
    "PAUSE": 0x13, "SCROLLLOCK": 0x91,
    "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "TAB": 0x09, "SPACE": 0x20, "CAPSLOCK": 0x14, "ENTER": 0x0D, "BACKSPACE": 0x08,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
})
# VK → (Set1 扫描码, 是否扩展键0xE0)。转发按键必须带扫描码(游戏原生输入层按
# 扫描码识别, scancode=0 会被忽略)。覆盖全部可做快捷键的单键; 鼠标侧键与 Pause
# (E1流) 无法用 keybd_event 干净转发, 不在表内。
VK_SCAN = {0x70 + i: (s, False) for i, s in enumerate(
    [0x3B, 0x3C, 0x3D, 0x3E, 0x3F, 0x40, 0x41, 0x42, 0x43, 0x44, 0x57, 0x58])}  # F1~F12
VK_SCAN.update({0x41 + i: (s, False) for i, s in enumerate(
    [0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26,
     0x32, 0x31, 0x18, 0x19, 0x10, 0x13, 0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D,
     0x15, 0x2C])})                                                              # A~Z
VK_SCAN.update({0x31: (0x02, False), 0x32: (0x03, False), 0x33: (0x04, False),
                0x34: (0x05, False), 0x35: (0x06, False), 0x36: (0x07, False),
                0x37: (0x08, False), 0x38: (0x09, False), 0x39: (0x0A, False),
                0x30: (0x0B, False)})                                            # 主键盘 1~0
VK_SCAN.update({0x60 + i: (s, False) for i, s in enumerate(
    [0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49])})              # 小键盘 0~9
VK_SCAN.update({0x6B: (0x4E, False), 0x6D: (0x4A, False), 0x6A: (0x37, False),
                0x6E: (0x53, False), 0x6F: (0x35, True)})                        # 小键盘 + - * . /
VK_SCAN.update({0x2D: (0x52, True), 0x2E: (0x53, True), 0x24: (0x47, True),
                0x23: (0x4F, True), 0x21: (0x49, True), 0x22: (0x51, True),
                0x26: (0x48, True), 0x28: (0x50, True), 0x25: (0x4B, True),
                0x27: (0x4D, True)})                                             # 编辑键区(扩展0xE0)
VK_SCAN.update({0x09: (0x0F, False), 0x20: (0x39, False), 0x0D: (0x1C, False),
                0x08: (0x0E, False), 0x14: (0x3A, False), 0x91: (0x46, False)})  # Tab 空格 回车 退格 大写 滚动锁
VK_SCAN.update({0xC0: (0x29, False), 0xBD: (0x0C, False), 0xBB: (0x0D, False),
                0xDB: (0x1A, False), 0xDD: (0x1B, False), 0xDC: (0x2B, False),
                0xBA: (0x27, False), 0xDE: (0x28, False), 0xBC: (0x33, False),
                0xBE: (0x34, False), 0xBF: (0x35, False)})                       # 符号键 `-=[]\;',./
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
# GDI/Win32 的 DC、位图、窗口句柄都是指针 —— 不声明类型会被 ctypes 当 int32 截断,
# 结果就是"拿到一个无效 DC, PrintWindow 静默失败"(按窗口抓那条路会白干)。这里一次声明清楚。
user32.GetDC.restype, user32.GetDC.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
user32.ReleaseDC.restype, user32.ReleaseDC.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]
user32.PrintWindow.restype, user32.PrintWindow.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.GetWindowRect.restype, user32.GetWindowRect.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]
user32.GetClientRect.restype, user32.GetClientRect.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype, gdi32.CreateCompatibleDC.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype, gdi32.CreateCompatibleBitmap.argtypes = (
    ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int])
gdi32.SelectObject.restype, gdi32.SelectObject.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteDC.restype, gdi32.DeleteDC.argtypes = ctypes.c_int, [ctypes.c_void_p]
gdi32.DeleteObject.restype, gdi32.DeleteObject.argtypes = ctypes.c_int, [ctypes.c_void_p]
gdi32.GetDIBits.restype, gdi32.GetDIBits.argtypes = (
    ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint])

try:
    user32.SetProcessDPIAware()   # 缩放显示器(125%/150%)下截屏与点击坐标保持物理像素一致(阶段2 toggle_teen 实战用法)
except Exception:
    pass


def parse_key(s):
    """'F8' / 'Ctrl+Alt+F5' / 'K' → (mods, vk)。支持单键与组合键"""
    mods, vk = 0, None
    for part in str(s).split("+"):
        p = part.strip()
        if not p:
            continue
        pl = p.lower()
        if pl in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif pl == "alt":
            mods |= MOD_ALT
        elif pl == "shift":
            mods |= MOD_SHIFT
        elif pl in ("win", "windows"):
            mods |= MOD_WIN
        elif p.upper() in VK_MAP:
            vk = VK_MAP[p.upper()]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
    return (mods | MOD_NOREPEAT, vk) if vk else (None, None)


# ---------------- 进程/内存 ----------------
PROC = "SC2_x64.exe"
_pm, _base = None, None
_DISPLAY_VER = None   # 战网显示版本号(get_pm 挂载时确定, 日志/界面用它对齐战网)
# 上次挂载失败的原因: None=正常 / "norun"=没找到游戏进程 / "version:x.y.z"=版本不符被拒。
# 调用方靠它区分"游戏没开"和"游戏开着但版本不在实测列表", 不再一律喊"游戏未运行"。
_ATTACH_ERR = None
# 测试/自动化保命闸: get_pm 是按进程名("SC2_x64.exe")挂载的, 不设闸的话跑一次单测
# 就可能对着玩家正在跑的游戏做内存操作(读+写都有)。测试脚本请在 import 前设
# SC2_UNCENSOR_NO_ATTACH=1(或像现有单测那样把 pymem 打桩)。
_NO_ATTACH = bool(os.environ.get("SC2_UNCENSOR_NO_ATTACH"))
_VER_WARNED = False   # 版本不符的详细提示只打一次(否则每按一次快捷键就重印5行)


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [("dwSignature", ctypes.c_uint32), ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32), ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32), ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32), ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32), ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32), ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32)]


def _exe_file_version(exe_path):
    """读 exe 的版本资源(语言无关的 VS_FIXEDFILEINFO), 返回 '主.次.修订.构建' 或 None"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe_path, 0, size, data):
            return None
        p, ln = ctypes.c_void_p(), ctypes.c_uint()
        if not ver.VerQueryValueW(data, "\\", ctypes.byref(p), ctypes.byref(ln)) or ln.value < 52:
            return None
        fi = ctypes.cast(p, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        return (f"{fi.dwFileVersionMS >> 16}.{fi.dwFileVersionMS & 0xFFFF}."
                f"{fi.dwFileVersionLS >> 16}.{fi.dwFileVersionLS & 0xFFFF}")
    except Exception:
        return None


def _load_offsets_override():
    """读 offsets.dat(可选文件): 返回 {exe版本号: {display, nop_sites, store, r}} 字典。
    文件缺失/损坏返回 {}——回退程序内置偏移, 不影响运行。"""
    try:
        data = json.load(open(os.path.join(BASE_DIR, "offsets.dat"), encoding="utf-8-sig"))
        return data.get("versions", {}) if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[{ts()}] [偏移] offsets.dat 读取失败({e}), 使用内置偏移")
        return {}


def _apply_offsets_override(ov):
    """把 offsets.dat 里某版本的偏移条目套用到模块全局(出错由调用方兜底)。"""
    global NOP_SITES, OFF_STORE, OFF_R
    NOP_SITES = [(int(o, 16), bytes.fromhex(b), f"覆盖{i}")
                 for i, (o, b) in enumerate(ov["nop_sites"])]
    OFF_STORE = int(ov["store"], 16)
    OFF_R = int(ov["r"], 16)


def get_pm():
    global _pm, _base, _ATTACH_ERR, _VER_WARNED
    if _NO_ATTACH:                      # 保命闸: 绝不连接任何进程
        _ATTACH_ERR = "norun"
        return None, None
    if _pm is not None:
        try:
            _pm.read_bytes(_base, 1)
            _ATTACH_ERR = None
            return _pm, _base
        except Exception:
            try:
                _pm.close()
            except Exception:
                pass
            _pm = None
    try:
        _pm = pymem.Pymem(PROC)
    except Exception:
        _ATTACH_ERR = "norun"
        return None, None
    mi = pymem.process.module_from_name(_pm.process_handle, PROC)
    _base = int(mi.lpBaseOfDll)
    # 版本核对 + offsets.dat 偏移覆盖: 文件条目优先, 否则回退内置偏移
    ver = None
    try:
        ver = _exe_file_version(psutil.Process(_pm.process_id).exe())
    except Exception:
        pass
    ov = _load_offsets_override().get(ver)
    ov_ok = False
    if ov:
        try:
            _apply_offsets_override(ov)
            ov_ok = True
            print(f"[{ts()}] [偏移] 已从 offsets.dat 载入 {ver} 的偏移")
        except Exception as e:
            # 条目格式错误=这个版本其实没有可用偏移。绝不能因为"文件里有它的键"
            # 就当成已知版本放行(否则会拿内置的旧版偏移去写新版本=可能写坏)
            print(f"[{ts()}] [偏移] offsets.dat 条目格式错误({e}), 使用内置偏移")
    else:
        # 无覆盖条目: 重置为内置偏移(防上次挂载的 dat 覆盖残留)
        global NOP_SITES, OFF_STORE, OFF_R
        NOP_SITES = [(o, b, n) for o, b, n in _BUILTIN_NOP_SITES]
        OFF_STORE, OFF_R = _BUILTIN_OFF_STORE, _BUILTIN_OFF_R
    known = ov_ok or (ver is not None and any(
        ver == b or ver.startswith(b + ".") for b in SUPPORTED_BUILDS))
    global _DISPLAY_VER
    _DISPLAY_VER = ((ov if ov_ok else {}) or {}).get("display") or (
        _BUILTIN_DISPLAY if ver == _BUILTIN_EXE_VER else ver)
    if not known and CFG.get("check_version", True):
        if not _VER_WARNED:      # 详细说明只打一次(否则每按一次快捷键就重印5行)
            _VER_WARNED = True
            print(f"[{ts()}] [版本] 检测到游戏版本 {ver or '未知'}, 本工具内置偏移仅实测支持 "
                  f"{'/'.join(SUPPORTED_BUILDS)}(64位)!")
            print(f"[{ts()}] [版本] 内存偏移按这些版本写死, 为防写坏已停止内存操作。")
            print(f"[{ts()}] [版本] 适配方法: 在 offsets.dat 加本版本条目(方法见 README.txt); "
                  f"或到发布页获取适配版; 或在 config.json 设 \"check_version\": false 自行尝试(风险自负)。")
        # 这里不发声: 声音由调用方(各快捷键流程的 fail())统一负责——状态查询
        # 不该响, 而"版本不符"对操作类快捷键仍会响一次(每操作一声)
        _ATTACH_ERR = "version:" + (ver or "未知")
        try:
            _pm.close()
        except Exception:
            pass
        _pm = None
        return None, None
    _ATTACH_ERR = None
    note = "" if known else "  (版本核对已关闭, 偏移未实测!)"
    disp = _DISPLAY_VER if _DISPLAY_VER != ver else ver
    print(f"[{ts()}] [连接] {PROC} 基址=0x{_base:X} "
          f"版本={ver or '未知'}{f' (战网显示: {disp})' if disp != ver else ''}{note}")
    return _pm, _base


def _no_game_msg():
    """挂载不上时给人看的原因(区分'游戏没开'和'游戏开着但版本被拒'——
    此前一律打印'游戏未运行', 版本不符时是误导)"""
    if _ATTACH_ERR and _ATTACH_ERR.startswith("version:"):
        return (f"游戏版本 {_ATTACH_ERR.split(':', 1)[1]} 不在实测支持列表, "
                f"已停止内存操作(见上一条版本提示)")
    return "游戏未运行"


def rt(off):
    return _base + off


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]   # 带毫秒, 便于精确分析耗时


_OP_START = 0.0        # 本次快捷键操作的开始时刻(看门狗计时用)
_WD_CONSOLE_WARNED = False  # 控制台卡住已提醒过(只提一次, 免得刷屏)
_WD_WARNED = False     # 本次操作是否已警告过超时
WATCHDOG_SEC = 20      # 一次操作超过这么久还没结束, 就往文件日志写警告
_BEEP_OP = False       # True=正在执行一次快捷键操作(这期间的声音先收集, 收尾只响一个)
_BEEP_PENDING = None   # 本次操作里最后一次想响的声音


def _beep_play(ok, kind):
    """真正发声: open=上行双音(打开) close=下行双音(关闭) fail=长低音(出问题)
    另有 on/off = **单音**(总开关用: 恢复/暂停) —— 总开关按一下只该听到"一声",
    双音会被听成"响了两下"(用户实测反馈)。其余成功操作仍按规格用双音。"""
    if not CFG.get("beep", True):
        return
    try:
        if kind == "open":
            winsound.Beep(1047, 80)    # C6
            winsound.Beep(1568, 120)   # G6
        elif kind == "close":
            winsound.Beep(1568, 80)
            winsound.Beep(1047, 120)
        elif kind == "on":
            winsound.Beep(1047, 90)    # 单音高: 其他快捷键已恢复
        elif kind == "off":
            winsound.Beep(784, 90)     # 单音低: 其他快捷键已暂停
        elif kind == "fail":
            winsound.Beep(400, 320)    # 长低音: 出错了
        elif kind == "skip":
            winsound.Beep(660, 90)     # 短促一声: 按规矩主动跳过(不是出错)
    except Exception:
        pass


def beep(ok=True, kind="done"):
    """按一次快捷键 = 响一次。操作进行中的 beep 先记下来(以最后一次为准), 操作收尾
    时统一响一个——这样"内部先报一个、外层再报一个"也只会听到一声(最终结论)。
    非快捷键路径(如 UI 直接调用)不在操作期内, 立即响。"""
    global _BEEP_PENDING
    if _BEEP_OP:
        _BEEP_PENDING = (ok, kind)
        return
    _beep_play(ok, kind)


def _beep_op_begin():
    global _BEEP_OP, _BEEP_PENDING, _OP_START, _WD_WARNED
    _BEEP_PENDING = None
    _BEEP_OP = True
    _OP_START = time.time()      # 看门狗计时起点
    _WD_WARNED = False


def _console_stuck():
    """控制台输出线程是否卡住(窗口被划选)——只影响显示, 但值得提醒一句。"""
    out = sys.stdout
    drain = getattr(getattr(out, "stream", None), "stuck", None)   # Tee(drain)
    if drain is None:
        drain = getattr(out, "stuck", None)                        # 直接是 drain
    return bool(drain)


def _watchdog():
    """看门狗: 一次快捷键操作超过 WATCHDOG_SEC 还没结束就写一行警告。
    专门对付"某个系统调用卡住导致假死": 主线程卡住后不再打印, 但这一行来自独立
    线程, 而且文件日志是同步写的(不受控制台卡顿影响), 所以一定会落盘——它是事后
    定位的唯一线索。2026-09-14 的控制台划选假死就是靠日志时间差定位的。"""
    global _WD_WARNED, _WD_CONSOLE_WARNED
    while True:
        time.sleep(2.0)
        try:
            if _console_stuck() and not _WD_CONSOLE_WARNED:
                _WD_CONSOLE_WARNED = True
                print(f"[{ts()}] [看门狗] 控制台窗口卡住了(多半是在窗口里划选了文字): "
                      f"窗口显示会暂停到按 Esc 为止, 工具运行和文件日志不受影响。")
            if _BEEP_OP and _OP_START and not _WD_WARNED and time.time() - _OP_START > WATCHDOG_SEC:
                _WD_WARNED = True
                print(f"[{ts()}] [看门狗] 本次操作已持续 {time.time() - _OP_START:.0f} 秒仍未结束, "
                      f"疑似卡在某个系统调用(控制台/OCR/窗口消息); 按键可能暂时无反应, "
                      f"详见 app/sc2_uncensor.log")
        except Exception:
            pass


def _beep_op_end():
    """一次快捷键操作收尾: 把收集到的最后一个声音响出来(本次没声音则保持静音)"""
    global _BEEP_OP, _BEEP_PENDING
    _BEEP_OP = False
    if _BEEP_PENDING is not None:
        _beep_play(*_BEEP_PENDING)
        _BEEP_PENDING = None


def skip(msg=None, code=3):
    """"主动跳过"的统一出口(对局内拒绝执行、勾选框蒙灰这类): 打印原因 + 短促提示音
    + 返回码。与 fail() 区分开——这不是出错, 是工具按规矩不做事, 不该吓用户。
    返回码约定: 0=成功 / 2=失败 / 3=主动跳过(UI 据此区分"没做成"和"按规矩没做")。"""
    if msg:
        print(f"[{ts()}] [提示] {msg}")
    beep(True, "skip")
    return code


def fail(msg=None, code=2):
    """失败统一出口: 打印原因 + 失败提示音 + 返回码, 快捷键流程用 `return fail(...)`。
    快捷键是盲操作(用户多半没在看这个窗口), 每个失败出口都必须出声——
    此前多条失败路径只打印不响, 出问题时会静默无反馈。"""
    if msg:
        print(f"[{ts()}] [错误] {msg}")
    beep(False, "fail")
    return code


def rd(addr, n):
    try:
        return _pm.read_bytes(addr, n)
    except Exception:
        return None


def read_state():
    """读 (NOP状态, 存储位, R三态)。**未挂载时返回 ("??", -1, -1) 而不抛异常**——
    UI 按规格每秒轮询它, 游戏没开/没连上时不能把 UI 的轮询回调打崩
    (此前会 TypeError: _base 是 None, rt() 做 None + off)。"""
    if _pm is None or _base is None:
        return "??", -1, -1
    nop = []
    for off, orig, _n in NOP_SITES:
        cur = rd(rt(off), 2)
        nop.append("nop" if cur == NOP_BYTE else ("orig" if cur == orig else "??"))
    store = rd(rt(OFF_STORE), 1)
    r = rd(rt(OFF_R), 4)
    return ("mixed" if len(set(nop)) > 1 else nop[0],
            store[0] if store else -1,
            int.from_bytes(r, "little") if r else -1)


def print_status():
    pm, base = get_pm()
    if pm is None:
        print(f"[{ts()}] [状态] {_no_game_msg()}")

        return
    nop, store, r = read_state()
    print(f"[{ts()}] ===== 状态 =====")
    print(f"  NOP渲染层 : {nop}  (nop=已解除强制 / orig=原始)")
    print(f"  存储位    : {store}  (0=未勾选 / 1=已勾选)")
    print(f"  R三态     : {r}  (0=和谐 1=完整 2=CHN强制)")
    print("  ================")



# ---------------- 内存配方 ----------------
def ensure_nop():
    pm, base = get_pm()
    if pm is None:
        print(f"[{ts()}] [NOP] {_no_game_msg()}")

        return False
    nop, _s, _r = read_state()
    if nop == "nop":
        return True
    if nop == "mixed":
        print(f"[{ts()}] [NOP] 状态异常, 不动")

        return False
    for off, orig, name in NOP_SITES:
        pm.write_bytes(rt(off), NOP_BYTE, 2)
    chk = all(rd(rt(off), 2) == NOP_BYTE for off, _o, _n in NOP_SITES)
    print(f"[{ts()}] [NOP] 打NOP {'OK' if chk else 'FAIL'}")

    return chk


def apply_memory_recipe():
    """NOP + 存储位0 + R=1 (暴露隐藏选项)。返回 bool=写入是否通过回读校验;
    校验不过=这次写入没生效(被杀软/权限拦、或游戏刚好退出), 调用方应中止后续
    点击流程(否则会对着没暴露的选项瞎点)。"""
    if not ensure_nop():
        return False
    pm, base = get_pm()
    if pm is None:
        print(f"[{ts()}] [配方] {_no_game_msg()}")
        return False
    pm.write_bytes(rt(OFF_STORE), bytes([0]), 1)
    pm.write_bytes(rt(OFF_R), (1).to_bytes(4, "little"), 4)
    r = rd(rt(OFF_R), 4)
    ok = bool(r) and r[0] == 1
    print(f"[{ts()}] [配方] NOP+存储位0+R=1 {'OK' if ok else 'FAIL'}"
          f"{'' if ok else '(写入没生效, 已中止: 见上方错误/杀软拦截)'}")

    return ok


def restore_memory_recipe():
    """关闭反和谐的内存部分: NOP 恢复原始字节(CHN强制路径回归) + 存储位0
    (让随后的一次点击落在'勾选'=和谐方向) + R=0(和谐)。返回 bool"""
    pm, base = get_pm()
    if pm is None:
        print(f"[{ts()}] [恢复内存] {_no_game_msg()}")

        return False
    for off, orig, _n in NOP_SITES:
        pm.write_bytes(rt(off), orig, 2)
    pm.write_bytes(rt(OFF_STORE), bytes([0]), 1)
    pm.write_bytes(rt(OFF_R), (0).to_bytes(4, "little"), 4)
    chk = all(rd(rt(off), 2) == orig for off, orig, _n in NOP_SITES)
    r = rd(rt(OFF_R), 4)
    ok = chk and bool(r) and r[0] == 0
    print(f"[{ts()}] [恢复内存] NOP原始+存储位0+R=0 {'OK' if ok else 'FAIL'}")

    return ok


# ---------------- OCR 定位栈 (源自阶段2 toggle_teen, 实战验证) ----------------
def _ocr_ps1_path():
    """OCR 的 PowerShell 回退脚本位置: 优先用 app 同目录那份(可随包分发, 不依赖
    仓库布局), 找不到再退开发仓库里的 workspace/phase1 原件。winocr 装不上或
    打包后 hook 不完整时靠它兜底。"""
    for p in (os.path.join(BASE_DIR, "win_ocr_json.ps1"),
              os.path.join(os.path.dirname(BASE_DIR), "workspace", "phase1", "win_ocr_json.ps1")):
        if os.path.exists(p):
            return p
    return os.path.join(BASE_DIR, "win_ocr_json.ps1")
OPT_BTN_POS = (0.500, 0.3319)   # 720p标定: ESC菜单"选项"按钮中心


def _debug_path(name):
    """debug 图统一放 app/debug/ 子目录(打包时整个排除, 不污染 app 根目录)"""
    d = os.path.join(BASE_DIR, "debug")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = BASE_DIR
    return os.path.join(d, name)


def find_sc2_hwnd():
    """先枚举可见顶层窗口(几十个)再按pid查进程名——psutil 遍历全部进程要几百ms,
    窗口优先只要几十ms(此前每次 OCR 的 sc2_rect 都在重付这笔钱=流程变慢主因)"""
    cands = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cands.append((hwnd, pid.value))
        return True
    user32.EnumWindows(CB(cb), 0)
    ours = []
    for hwnd, pid in cands:
        try:
            if psutil.Process(pid).name().lower() == PROC.lower():
                ours.append(hwnd)
        except Exception:
            continue
    if not ours:
        return None
    # 2026-09-14 修正: 原来"返回第一个属于 SC2 的可见带标题窗口"。独占全屏/带启动器时,
    # 进程里可能还有别的带标题窗口, 于是返回的 hwnd **不是**真正前台那个 —— 后续
    # activate() 会误判"游戏不在前台"去抢前台, 而这正是把全屏游戏挤成最小化的元凶。
    fg = user32.GetForegroundWindow()
    if fg in ours:
        return fg                      # 前台那个就是游戏窗口: 最可靠
    def _area(h):
        r = _RECT()
        return ((r.right - r.left) * (r.bottom - r.top)) if user32.GetWindowRect(h, ctypes.byref(r)) else 0
    return max(ours, key=_area)        # 否则取最大的(游戏窗口总比辅助窗口大)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_SC2_RECT = (None, 0.0)   # (客户区全局坐标(l,t,r,b) 或 None, 缓存时刻)


def sc2_rect():
    """SC2 客户区的全局屏幕坐标 (l,t,r,b), 2 秒缓存。
    多屏优化: SC2 在哪个屏, 截屏/点击坐标就落在哪个屏(此前只认主屏, 副屏全错)。
    进程DPI已声明Aware, 这里返回物理像素, 与 ImageGrab/SetCursorPos 一致。"""
    global _SC2_RECT
    if time.time() - _SC2_RECT[1] < 2.0:
        return _SC2_RECT[0]
    rect = None
    hwnd = find_sc2_hwnd()
    if hwnd:
        cr, pt = _RECT(), wt.POINT()
        if user32.GetClientRect(hwnd, ctypes.byref(cr)) and user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            if cr.right > 100 and cr.bottom > 100:      # 游戏客户端不会小于这个
                rect = (pt.x, pt.y, pt.x + cr.right, pt.y + cr.bottom)
    _SC2_RECT = (rect, time.time())
    return rect


def _primary_rect():
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def _crop_rect():
    """OCR/像素扫描的中央裁剪区: SC2 客户区的 30%~70% × 20%~96%。
    返回 (x0, y0, x1, y1, 客户区宽, 客户区高), 坐标为全局屏幕坐标。"""
    l, t, r, b = sc2_rect() or _primary_rect()
    w, h = r - l, b - t
    return (l + int(w * 0.30), t + int(h * 0.20),
            l + int(w * 0.70), t + int(h * 0.96), w, h)


def _frac_pt(fx, fy):
    """SC2 客户区内比例点(fx,fy ∈ 0~1)的全局屏幕坐标——标定类点击多屏安全版"""
    l, t, r, b = sc2_rect() or _primary_rect()
    return l + int((r - l) * fx), t + int((b - t) * fy)


_LAST_FOCUS = "?"      # 上一次记录的(是否前台, 是否最小化), 只在变化时打日志


def _focus_note(tag):
    """在输入动作前后记一行"游戏是否仍在前台/有没有被最小化" —— 只在状态**变化**时打,
    所以不会刷屏; 万一某一步把全屏游戏挤成最小化, 日志里能直接指认是哪一个动作。"""
    global _LAST_FOCUS
    try:
        hwnd = find_sc2_hwnd()
        cur = "无窗口" if not hwnd else (
            "游戏在前台" if user32.GetForegroundWindow() == hwnd
            else ("已最小化" if user32.IsIconic(hwnd) else "前台是别的窗口"))
        if cur == _LAST_FOCUS:
            return
        _LAST_FOCUS = cur
        print(f"[{ts()}] [焦点] {tag} -> {cur}")
    except Exception:
        pass


def _win_title(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(hwnd, buf, 128)
    return buf.value


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def _is_fullscreen(hwnd):
    """窗口是否铺满它所在显示器的整个屏幕(独占全屏 / 无边框最大化)。
    2026-09-14 用户实测: 本机"窗口化最大化"一切正常, 新电脑"独占全屏"则游戏被挤到
    桌面且自动点击失败 —— 两者必须区别对待。"""
    try:
        r = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        mon = user32.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
        if not mon:
            return False
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        return (r.left <= m.left and r.top <= m.top
                and r.right >= m.right and r.bottom >= m.bottom)
    except Exception:
        return False


def _exclusive_fullscreen(hwnd):
    """全屏窗口里, 是不是**独占全屏**(真全屏)?

    为什么要再分一层: 两类"全屏"的前台行为完全不同 ——
      · 无边框窗口(铺满屏幕但仍是普通窗口): 和普通窗口一样能抢前台, ALT 小技巧完全可用;
      · 独占全屏: 被系统切走就会**最小化**(用户实测"游戏被挤到桌面"), 只能用温和手段。
    以前把两者一律当"独占全屏"处理, 无边框用户会遇到"明明能切前台, 工具却说切不过去、
    还劝他改窗口化"。判据是**桌面分辨率**:
      · 桌面分辨率 ≠ 显示器原生分辨率 → 显示模式被改过 → 独占全屏;
      · 桌面分辨率 = 原生分辨率(当前几乎必然, Win10+ 全屏优化会把独占全屏也做成
        无模式切换)→ **保守地按"不能强制"处理**, 行为与修复前一致 —— 宁可少用一次
        ALT, 也不能再让用户的游戏被挤到桌面(那条事故的教训)。
    """
    if not _is_fullscreen(hwnd):
        return False
    try:
        mon = user32.MonitorFromWindow(hwnd, 2)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not mon or not user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return True                     # 判不了: 按最保守的来(与修复前一致)
        m = mi.rcMonitor
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return (sw, sh) != (m.right - m.left, m.bottom - m.top)
    except Exception:
        return True


def _click_gate():
    """点击前的安全闸: 游戏窗口必须存在、没被最小化、且**就是当前前台窗口**。

    为什么必须有(2026-09-14 用户实测): 独占全屏的游戏一旦被系统切走就会最小化, 这时
    屏幕上是桌面/我们自己的界面 —— OCR 会把**我们自己界面上的"选项"**当成游戏的,
    接着"照着点", 用户看到的就是"跳回桌面后去点这个工具自己的选项"。宁可不点。
    返回 None=可以点; 返回字符串=不能点的原因。
    """
    hwnd = find_sc2_hwnd()
    if not hwnd:
        return "找不到游戏窗口"
    if user32.IsIconic(hwnd):
        return "游戏窗口已最小化"
    if user32.GetForegroundWindow() != hwnd:
        return "游戏窗口不在前台(前台是别的窗口)"
    return None


def activate(hwnd, force=False):
    """把 SC2 拉到前台。已在前台时直接返回 True(惰性激活用)。

    2026-09-14 两轮加固:
      · 根因(用户实测): **独占全屏**下, 别的进程抢前台会把游戏挤成最小化 —— 屏幕露出
        桌面/我们自己的界面, OCR 随后读到自己界面的字并"照着点"。本机是窗口化最大化
        所以一直没暴露。对策: 只有**独占全屏**才"不做 ALT 抢前台那套"(只温和试一次),
        抢不到就明确提示"请改窗口化/无边框", 绝不硬来; 窗口化与**无边框**(2026-09-16
        起用 _exclusive_fullscreen 区分)仍走原来实测有效的 ALT 小技巧 —— 无边框窗口
        本来就能正常抢前台, 以前被误判成独占全屏, 白丢一次能成的机会。
      · ALT 用 try/finally 保证抬起(卡住的 ALT + 注入按键 = Alt+X, 而 Alt+Enter 正是
        全屏/窗口切换); 只有真最小化时才 ShowWindow(SW_RESTORE)(对最大化窗口调它会
        取消最大化, 看着就是"跳桌面")。
    返回 True=游戏确实在前台(可以安全点击); False=没切过去(调用方必须放弃点击)。
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    iconic = bool(user32.IsIconic(hwnd))
    full = _is_fullscreen(hwnd)
    exclusive = full and _exclusive_fullscreen(hwnd)      # 只有独占全屏才禁止 ALT 抢前台
    print(f"[{ts()}] [激活] 目标窗口='{_win_title(hwnd)}' 最小化={iconic} 全屏={full}"
          f"{'(独占)' if exclusive else '(无边框)' if full else ''} "
          f"前台={user32.GetForegroundWindow()} 目标={hwnd}")
    if iconic:
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE: 只对"真的最小化"的窗口用
        time.sleep(0.15)
    if exclusive and not force:
        user32.SetForegroundWindow(hwnd)     # 独占全屏: 只温和尝试, 不按 ALT、不动窗口
        time.sleep(0.2)
    else:
        try:
            user32.keybd_event(0x12, 0, 0, 0)   # ALT 按下(绕前台锁, 窗口化下实测有效)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.keybd_event(0x12, 0, 2, 0)   # 无论成败都抬起 ALT
        time.sleep(0.15)
        if user32.GetForegroundWindow() != hwnd:
            fg = user32.GetForegroundWindow()
            ft = user32.GetWindowThreadProcessId(fg, None)
            ct = user32.GetWindowThreadProcessId(hwnd, None)
            user32.AttachThreadInput(ct, ft, True)
            try:
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(ct, ft, False)
        time.sleep(0.1)
    if user32.GetForegroundWindow() == hwnd:
        return True
    print(f"[{ts()}] [激活] 未能把游戏切到前台(当前前台={user32.GetForegroundWindow()})"
          + ("；**独占全屏下系统不允许别的程序抢前台**, 请把游戏改成'窗口化'或"
             "'无边框窗口'再用自动模式(手动模式 F8 不受影响)" if exclusive
             else "；可能是系统前台锁, 手动点一下游戏窗口再试"))
    return False


def send_key(vk):
    _focus_note(f"发送按键 0x{vk:02X} 前")
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, 2, 0)
    _focus_note(f"发送按键 0x{vk:02X} 后")


def send_esc():
    send_key(0x1B)


def click(x, y):
    _focus_note(f"点击({int(x)},{int(y)}) 前")
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    user32.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.03)
    user32.mouse_event(4, 0, 0, 0, 0)
    _focus_note(f"点击({int(x)},{int(y)}) 后")


def grab_retry(bbox=None, tries=8):
    """bbox 为全局屏幕坐标(多屏下可为负/超主屏范围), all_screens=True 必须"""
    for _ in range(tries):
        try:
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except OSError:
            user32.keybd_event(0x7E, 0, 0, 0)
            user32.keybd_event(0x7E, 0, 2, 0)
            time.sleep(1.5)
    raise RuntimeError("屏幕截取失败(休眠/锁定?)")


def _looks_blank(img):
    """图是不是"什么都没截到"(全黑)。独占全屏下 GDI 桌面截图常是全黑 —— 这时要改用
    按窗口抓(PrintWindow)。判据故意宽松: 灰度最大值 <=6 才算黑, 免得把暗场景误判。"""
    try:
        return img.convert("L").getextrema()[1] <= 6
    except Exception:
        return False


DARK_P95 = 24          # "整片暗到没法判读"的门槛(灰度 p95): 低于它当成截图没抓到


def _pixel_stats(img):
    """PIL 图 → (像素数, 亮度 p95, 暗像素占比)。给"这张图能不能判读"当依据。

    用 PIL 的直方图算(纯 C 循环), 比逐像素 getpixel 快一个量级 —— 判定勾选框的
    那条 90×28 小带子每轮都要过一次, 不值得为它慢下来。
    亮度 = (R*299 + G*587 + B*114) / 1000(和人眼感受一致的近似)。
    """
    try:
        im = img.convert("L")
        n = im.width * im.height
        if n <= 0:
            return 0, 0, 1.0
        h = im.histogram()
        acc = 0
        p95 = 255
        for v in range(256):
            acc += h[v]
            if acc >= n * 0.95:
                p95 = v
                break
        dark = sum(h[:DARK_P95]) / float(n)
        return n, p95, dark
    except Exception:
        return 0, 0, 1.0


def grab_window(hwnd):
    """按窗口抓图(PrintWindow), 返回 PIL RGB 或 None。

    flag 取自 zzzod(绝区零一条龙)的同名实现: PW_CLIENTONLY(0x1) | PW_RENDERFULLCONTENT(0x2)。
    它用这套 API 在"无边框全屏"下工作良好; 我们据此作为独占全屏/桌面截图全黑时的兜底。
    因为带 CLIENTONLY, 位图原点=客户区原点, 与 sc2_rect() 的坐标体系一致(裁剪直接相减)。
    """
    try:
        r = _RECT()
        if not hwnd or not user32.GetClientRect(hwnd, ctypes.byref(r)):
            return None
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0 or w * h > 16 * 1024 * 1024:
            return None
        hdc = user32.GetDC(hwnd)
        if not hdc:
            return None
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mdc, bmp)
        try:
            if not user32.PrintWindow(hwnd, mdc, 3):      # CLIENTONLY | RENDERFULLCONTENT
                return None

            class _BIH(ctypes.Structure):
                _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                            ("biClrImportant", ctypes.c_uint32)]
            bi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)   # 负高度=自上而下
            buf = ctypes.create_string_buffer(w * h * 4)
            if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
                return None
            from PIL import Image
            return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        finally:
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mdc)
            user32.ReleaseDC(hwnd, hdc)
    except Exception as e:
        print(f"[{ts()}] [截图] 按窗口抓失败({e})")
        return None


def grab_screen(bbox=None):
    """截图。三种方式(可用 config.json 的 "screenshot" 强制指定):
      auto(默认) = 先桌面 GDI 截屏, 疑似全黑(独占全屏常见)就改用按窗口抓
      bitblt     = 只用桌面 GDI 截屏(最快)
      printwindow= 只用按窗口抓(和绝区零一条龙同款 API+flag; 独占全屏/黑屏时用它)
    bbox 是全局屏幕坐标; 窗口抓那条路会按"客户区原点"换算后再裁剪。
    """
    method = str(CFG.get("screenshot", "auto")).lower()
    hwnd = find_sc2_hwnd() if method == "printwindow" else None
    if hwnd is not None:
        win = grab_window(hwnd)
        if win is not None:
            return _crop_to_bbox(win, hwnd, bbox)
    if method == "printwindow":
        print(f"[{ts()}] [截图] 指定的按窗口抓失败, 退回桌面截屏")
    img = grab_retry(bbox=bbox)
    if method == "bitblt" or not _looks_blank(img):
        return img
    if hwnd is None:
        hwnd = find_sc2_hwnd()
    win = grab_window(hwnd) if hwnd else None
    if win is None:
        print(f"[{ts()}] [截图] 桌面截图疑似全黑({_pixel_stats(img)[1]}/255), 按窗口抓也未成功 —— "
              f"这个全屏模式可能截不到画面(把日志发回可继续定位; "
              f"或在 config.json 设 \"screenshot\": \"printwindow\" 再试)")
        return img
    print(f"[{ts()}] [截图] 桌面截图疑似全黑({_pixel_stats(img)[1]}/255), "
          f"已改用按窗口抓(PrintWindow)")
    got = _crop_to_bbox(win, hwnd, bbox)
    # 兜底那条路也可能抓到黑帧(实测不罕见: 换显示模式/系统弹安全桌面时两种都黑)。
    # 判读不出内容的话把亮度打出来 —— 否则调用方只能看到"没找到/蒙灰"这种误判结论。
    _n, _p95, _dark = _pixel_stats(got)
    if _p95 <= DARK_P95:
        print(f"[{ts()}] [截图] 按窗口抓到的内容也几乎全黑(亮度{_p95}/255, "
              f"暗像素{_dark * 100:.0f}%) —— 这一帧判读不可靠, 稍后重试")
    return got


def grab_alt(bbox, bad_method):
    """用**另一种**截图方式再抓一次(给"抓到黑帧"的调用方兜底)。

    桌面截屏与按窗口抓各有各的失灵场景(桌面截屏在独占全屏/安全桌面全黑; PrintWindow
    对带反外挂的进程会被拒绝、也可能整幅黑), 所以一种不行就换另一种, 而不是拿黑帧
    继续往下判。返回 (图, 实际用的方式) / (None, None)。
    """
    want_pw = (bad_method != "window")
    if want_pw:
        hwnd = find_sc2_hwnd()
        win = grab_window(hwnd) if hwnd else None
        if win is not None:
            return _crop_to_bbox(win, hwnd, bbox), "window"
        # 窗口抓不可用(反外挂拒绝/句柄没了) → 退回桌面截屏
    return grab_retry(bbox=bbox), "desktop"


def _crop_to_bbox(win, hwnd, bbox):
    """把"整窗图"按全局屏幕 bbox 裁到客户区对应位置(窗口抓用的是客户区原点)"""
    if not bbox:
        return win
    l, t, r, b = sc2_rect() or _primary_rect()
    x0 = max(0, bbox[0] - l)
    y0 = max(0, bbox[1] - t)
    return win.crop((x0, y0, x0 + (bbox[2] - bbox[0]), y0 + (bbox[3] - bbox[1])))


_OCR_ENGINE = None


def _ocr_recognize(pil_img):
    """winocr.recognize_pil_sync 的引擎复用版: 原版每调一次都新建 OcrEngine
    +asyncio 事件循环, 这里引擎只建一次, 输出结构完全相同(dict lines/words)。"""
    global _OCR_ENGINE
    import asyncio
    import winocr
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    if _OCR_ENGINE is None:
        _OCR_ENGINE = (OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
                       or OcrEngine.try_create_from_user_profile_languages())
    rgba = pil_img.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    sb = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.RGBA8, rgba.width, rgba.height)
    result = asyncio.run(winocr.to_coroutine(_OCR_ENGINE.recognize_async(sb)))
    return winocr.picklify(result)


def ocr_center(scale=2):
    """OCR 屏幕中央区域(对话框所在), 返回 ([{text,x,y,w,h}屏幕坐标], (屏宽,屏高))
    主路径: winocr 进程内直调 Windows 引擎(~0.4s/次, 无子进程开销)
    回退:   PowerShell 子进程(winocr 不可用时)
    scale: 送识别前的放大倍数。游戏内文字小(约19px高), 1倍识别率骤降
    (v1.1实测: '减少暴力表现'被读成'减少暴力表±'), 2倍为阶段2实战值。
    实测离线截图: scale=1 识别0/1张, scale=2/3 全中, 单次仅慢~0.1s。"""
    x0, y0, x1, y1, W, H = _crop_rect()
    crop = grab_screen(bbox=(x0, y0, x1, y1))
    crop_s = crop if scale == 1 else crop.resize((int((x1 - x0) * scale), int((y1 - y0) * scale)))
    if CFG.get("debug"):
        crop_s.save(_debug_path("debug_ocr_input.png"))
    lines = None
    try:
        import winocr   # P0优化: 进程内OCR, 免PowerShell子进程(见 docs/OCR优化.md)
        res = _ocr_recognize(crop_s)
        out_lines = []
        for ln in res.get("lines", []):
            words = ln.get("words", [])
            xs, ys, xe, ye = [], [], [], []
            for w in words:
                br = w.get("bounding_rect", {})
                X, Y = br.get("x", 0) / scale, br.get("y", 0) / scale
                WD, HT = br.get("width", 0) / scale, br.get("height", 0) / scale
                xs.append(X); ys.append(Y); xe.append(X + WD); ye.append(Y + HT)
            if not xs:
                continue
            out_lines.append({"text": ln.get("text", ""),
                              "x": x0 + int(min(xs)), "y": y0 + int(min(ys)),
                              "w": max(1, int(max(xe) - min(xs))),
                              "h": max(1, int(max(ye) - min(ys)))})
        lines = out_lines          # 0行也是有效结果(如对局画面无文字), 走PS回退纯属浪费
        print(f"[{ts()}] [OCR] 进程内引擎 {len(lines)}行 scale={scale}")
    except ImportError:
        pass                                     # winocr 未安装 → 回退
    except Exception as e:
        print(f"[{ts()}] [OCR] 进程内OCR失败({e}), 回退PowerShell")
    if lines is None:                            # PowerShell 回退路径
        import tempfile
        import subprocess
        crop_s = crop
        if scale != 1:
            crop_s = crop.resize((int((x1 - x0) * scale), int((y1 - y0) * scale)))
        path = os.path.join(tempfile.gettempdir(), "sc2_uncensor_ocr.png")
        crop_s.save(path)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _ocr_ps1_path(), path],
            capture_output=True, timeout=90)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        if not out:
            raise RuntimeError("OCR 无输出: " + (r.stderr or b"").decode("gbk", "replace")[:200])
        data = json.loads(out)
        if not data.get("ok"):
            raise RuntimeError("OCR 失败: " + str(data.get("error")))
        lines = data["lines"]
        for ln in lines:
            ln["x"] = x0 + int(ln["x"] / scale)
            ln["y"] = y0 + int(ln["y"] / scale)
            ln["w"] = max(1, int(ln["w"] / scale))
            ln["h"] = max(1, int(ln["h"] / scale))
    return lines, (W, H)


def _norm(t):
    return t.replace(" ", "").replace(":", "").replace("：", "")


def find_line(lines, key):
    for ln in lines:
        if _norm(ln["text"]) == key:
            return ln
    for ln in lines:
        if key in _norm(ln["text"]) and len(_norm(ln["text"])) <= len(key) + 2:
            return ln
    for ln in lines:
        if key in _norm(ln["text"]):
            return ln
    return None


def center(ln):
    return ln["x"] + ln["w"] // 2, ln["y"] + ln["h"] // 2


_KEYS = ("减少暴力表现", "暴力", "画面", "选项", "返回游戏", "菜单", "接受", "取消")


def _match_keys(lines, keys=_KEYS):
    """对一次 OCR 的全部行做关键词匹配, 返回 {key: 行}。
    OCR 每次调用已把整屏文字一次性识别出来(所有行带坐标), 这里只是对几十个
    短字符串做比较——5个词实测约0.5ms, 对比识别本身~350ms可忽略,
    多个关键词不会增加 OCR 次数或可感知耗时(分散调用合并成一次遍历只为精简)。
    优先级同 find_line: 整行精确 > 短行包含 > 任意包含。"""
    normed = [(_norm(ln["text"]), ln) for ln in lines]
    hits = {}
    for k in keys:
        for t, ln in normed:
            if t == k:
                hits[k] = ln
                break
    for k in keys:
        if k in hits:
            continue
        for t, ln in normed:
            if k in t and len(t) <= len(k) + 2:
                hits[k] = ln
                break
    for k in keys:
        if k in hits:
            continue
        for t, ln in normed:
            if k in t:
                hits[k] = ln
                break
    return hits


# ---------------- 勾选框定位 + 验证点击 ----------------
def _blue_clusters(rgb):
    """在 PIL RGB 图里找'亮蓝边框'块, 返回 [(cx,cy,w,h)](图内坐标)。
    特征(阶段1实测): B>=140 且 B-R>=40 且 B-G>=30; 勾选框边框约16~20px@1080p。
    行投影分带→带内列投影分簇: 勾选框互不相连, 投影聚类足够。

    实现只用 PIL 的通道运算(全是 C 速度), 不用 numpy —— 发布包里 numpy 一家要占
    约 25MB(openblas 一个 DLL 就 19.6MB), 而这里只需要"逐像素比大小+投影"。等价写法:
      · 三个条件用 point() 做阈值 + subtract() 求差(自带下限 0) + multiply() 当逻辑与
      · 行/列投影用 bytes 切片求和(也是 C 速度), 720p 整屏约 10ms
    行为与旧 numpy 版逐像素一致(离线回归用例 + scripts 里的 A/B 对比守着)。"""
    from PIL import ImageChops
    r, g, b = rgb.split()[:3]
    lut_b = [255 if v >= 140 else 0 for v in range(256)]
    lut_br = [255 if v >= 40 else 0 for v in range(256)]
    lut_bg = [255 if v >= 30 else 0 for v in range(256)]
    mask = ImageChops.multiply(                       # 相乘 = 逻辑与(255 才留)
        ImageChops.multiply(b.point(lut_b), ImageChops.subtract(b, r).point(lut_br)),
        ImageChops.subtract(b, g).point(lut_bg))
    W, H = mask.size
    px = mask.tobytes()                               # 每像素 1 字节(0 或 255)
    if not any(px):
        return []
    rows = [y for y in range(H) if sum(px[y * W:(y + 1) * W])]
    bands, start = [], rows[0]
    for i in range(1, len(rows)):
        if rows[i] - rows[i - 1] > 4:
            bands.append((start, rows[i - 1]))
            start = rows[i]
    bands.append((start, rows[-1]))
    out = []
    for y0, y1 in bands:
        cols = [x for x in range(W) if sum(px[y0 * W + x:(y1 + 1) * W:W])]
        cstart = cols[0]
        for i in range(1, len(cols)):
            if cols[i] - cols[i - 1] > 4:
                out.append((y0, y1, cstart, cols[i - 1]))
                cstart = cols[i]
        out.append((y0, y1, cstart, cols[-1]))
    return [(x0 + (x1 - x0 + 1) // 2, y0 + (y1 - y0 + 1) // 2, x1 - x0 + 1, y1 - y0 + 1)
            for y0, y1, x0, x1 in out]


def pixel_find_checkbox(W, H):
    """纯像素兜底: OCR 连模糊匹配都找不到标签行时, 在对话框区直接扫亮蓝勾选框。
    画面页里只有'减少暴力表现'的勾选框是亮蓝色(其余为金铜色), 不需要认字。
    多个候选时取最上面一个; 点没点准由存储位回读裁决(点错立知)。"""
    x0, y0, x1, y1 = _crop_rect()[:4]
    img = grab_screen(bbox=(x0, y0, x1, y1)).convert("RGB")
    if CFG.get("debug"):
        img.save(_debug_path("debug_pixel_scan.png"))
    ok = [(x0 + cx, y0 + cy, w, h) for cx, cy, w, h in _blue_clusters(img)
          if 7 <= w <= 50 and 7 <= h <= 50]        # 勾选框尺寸窗(720p~4K), 滤掉蓝色大按钮/进度条
    if not ok:
        print(f"[{ts()}] [定位] 纯像素兜底: 未找到蓝色勾选框")
        return None
    x, y = min(ok, key=lambda p: p[1])[:2]         # 多候选取最上面
    print(f"[{ts()}] [定位] 纯像素兜底: {len(ok)}个蓝框候选, 取y最小 ({x},{y})")
    return x, y


def _count_blue(xs, bw):
    """候选列号里, 有多少落在勾选框一带(图中心 ±25)。bw = 图宽(中心列 = bw//2)。"""
    c = bw // 2
    return len([xx for xx in xs if abs(xx - c) <= 25])


def _blue_cols(band):
    """把一条小图里的"亮蓝边框"像素列号(图内 x, 以图中心为 45)找出来"""
    px = band.convert("RGB").load()
    bw, bh = band.size
    xs = []
    for yy in range(bh):
        for xx in range(bw):
            r, g, b = px[xx, yy]
            if b >= 140 and b - r >= 40 and b - g >= 30:   # 亮蓝边框
                xs.append(xx)
    return xs


def refine_checkbox(lab, W, H):
    """从'减少暴力表现'标签行推算勾选框中心: 标签右缘+标定偏移, 再用亮蓝边框
    像素细化x。(从 ocr_find_checkbox 拆出, 供定位一次后两次点击复用)

    返回 (x,y) / SKIPPED(勾选框蒙灰锁定, 按规矩不点) / None(定位失败)

    ⚠️ "没有亮蓝边框"必须在**画面可判读**的前提下才能当成"蒙灰": 截图全黑时同样一个
    蓝像素都找不到, 那时判蒙灰就是冤案(实测 2026-09-18: 换显示模式/系统安全桌面导致
    PrintWindow 抓到黑帧, 主界面正常却被判成"对局内锁定", 用户看到的提示完全误导)。
    现在先看像素统计: 整片暗到没法判读 → 换另一种截图方式重试一次; 还是不行就退回
    **标签推算的估计值**交给调用方(点击后存储位回读会裁决, 派不上用场也只是白点一次,
    比谎报"对局内"好——_click_gate 已保证游戏就在前台, 点空的代价可控)。
    """
    y = lab["y"] + lab["h"] // 2
    x_guess = lab["x"] + lab["w"] + int(28 * (W / 1280.0))   # 1080p标定偏移
    try:
        band = grab_screen(bbox=(x_guess - 45, y - 14, x_guess + 45, y + 14))
        if CFG.get("debug"):
            band.save(_debug_path("debug_band.png"))
        _n, p95, dark = _pixel_stats(band)
        bw0 = band.size[0]
        xs = _blue_cols(band)
        hits = _count_blue(xs, bw0)
        if hits < 10 and p95 <= DARK_P95:
            # 这一帧里"什么都没有"——先换另一种截图方式再判, 别急着下"蒙灰"的结论
            print(f"[{ts()}] [定位] 勾选框一带的画面几乎全黑(亮度{p95}/255, 暗像素"
                  f"{dark * 100:.0f}%), 换另一种截图方式重试…")
            band2, how2 = grab_alt((x_guess - 45, y - 14, x_guess + 45, y + 14), "desktop")
            if band2 is not None:
                _n2, p95b, darkb = _pixel_stats(band2)
                bw2 = band2.size[0]
                xs2 = _blue_cols(band2)
                hits2 = _count_blue(xs2, bw2)
                if p95b > DARK_P95:
                    band, p95, dark, bw0 = band2, p95b, darkb, bw2
                    xs, hits = xs2, hits2
                    print(f"[{ts()}] [定位] 另一种方式({how2})这一次读到了画面(亮度{p95b}/255)")
                else:
                    print(f"[{ts()}] [定位] 另一种方式({how2})也是黑的(亮度{p95b}/255)")
        if hits < 10:
            if p95 <= DARK_P95:
                # 怎么抓都是黑的: 判不了. 用标签推算的估计值(点击后回读会裁决),
                # 绝不谎报"勾选框蒙灰/对局内" —— 那会让用户以为得打完了才能用
                x = x_guess
                print(f"[{ts()}] [定位] 画面读不出来(两种截图方式都黑), 用标签推算的"
                      f"({x},{y})且后续以存储位回读为准(不当作蒙灰)")
            else:
                # 画面正常、就是没有亮蓝边框 = 勾选框真蒙灰(对局内锁定)。
                # 对局内实测: 蒙灰标签文字 OCR 仍可读, 只有边框像素能区分 → 拒绝点击。
                # 返回 SKIPPED(不是 None): 这是"按规矩跳过", 提示音用短促音而非失败长低音。
                print(f"[{ts()}] [定位] 标签已识别但勾选框处无亮蓝边框"
                      f"(蒙灰=对局内锁定? 亮度{p95}/255, 暗像素{dark * 100:.0f}%), 不点击")
                return SKIPPED
        else:
            keep = [xx for xx in xs if abs(xx - bw0 // 2) <= 25]
            x = x_guess - 45 + (min(keep) + max(keep)) // 2
            print(f"[{ts()}] [定位] 行y={y} x={x} (像素细化)")
    except Exception as e:
        x = x_guess
        print(f"[{ts()}] [定位] 行y={y} x={x} (像素细化失败:{e})")
    return x, y


def ocr_find_checkbox():
    """OCR找'减少暴力表现'行 → 亮蓝边框像素细化勾选框x → 屏幕坐标
    标签整行漏识别时退纯像素兜底(画面页唯一蓝框)。
    返回 (x,y) / SKIPPED(勾选框蒙灰锁定) / None(没找到)"""
    lines, (W, H) = ocr_center()
    hits = _match_keys(lines, ("减少暴力表现", "暴力"))
    lab = hits.get("减少暴力表现") or hits.get("暴力")
    if lab is None:
        try:
            return pixel_find_checkbox(W, H)
        except Exception as e:
            print(f"[{ts()}] [定位] 纯像素兜底失败({e})")
            return None
    return refine_checkbox(lab, W, H)


def _wait_store_change(before, timeout_ms):
    """点击后轮询存储位翻转(游戏回写通常几十毫秒), 翻转即刻返回 True;
    到 timeout_ms 仍没翻转才判未命中——比死等固定间隔又快又稳。"""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        cur = rd(rt(OFF_STORE), 1)
        if cur and cur[0] != before:
            return True
        time.sleep(0.015)
    return False


VERIFY_CLICK_MIN_MS = 50      # 下限: 配成 0/负数会让"等翻转"立刻超时 → 每次点击都判未命中


def _verify_click_ms():
    """config.verify_click_ms 的安全取值(带下限)。值本身是"等存储位翻转的最长毫秒数",
    写 0 就等于永远不等 —— 那种情况下点击其实命中了也会被判未命中, 所以这里兜一下。"""
    try:
        return max(VERIFY_CLICK_MIN_MS, int(CFG.get("verify_click_ms", 300)))
    except Exception:
        return 300


def click_checkbox_verified(pos=None):
    """点击勾选框 + 存储位回读验证命中。
    pos=已定位坐标时直接复用(对话框不动, 免一次 OCR+截屏, 省约0.7s)"""
    b = rd(rt(OFF_STORE), 1)
    if not b:                                   # 游戏中途退出/内存不可读
        print(f"[{ts()}] [点击] 存储位不可读(游戏已退出?)")

        return False
    before = b[0]
    why = _click_gate()          # 安全闸: 游戏不在前台/被最小化时绝不点(否则会点到别的窗口)
    if why:
        print(f"[{ts()}] [点击] 已阻止: {why} —— 宁可不点, 也不对着别的窗口乱点")
        return False
    if pos is None:
        pos = ocr_find_checkbox()
        if pos is SKIPPED:      # 勾选框蒙灰锁定: 不点(不是故障, 交给调用方发短促音)
            print(f"[{ts()}] [点击] 勾选框蒙灰(选项锁定, 多在对局内), 不点击")
            return False
        if pos is None:
            return False
    click(*pos)
    hit = _wait_store_change(before, _verify_click_ms())
    time.sleep(0.05)   # 留一帧余量再走下一步(第二次点击/收尾)
    if not hit:
        try:       # 点了没翻转: 看看勾选框处有没有亮蓝边框(没有=蒙灰, 对局内锁定)
            band = grab_screen(bbox=(int(pos[0]) - 20, int(pos[1]) - 20,
                                    int(pos[0]) + 20, int(pos[1]) + 20)).convert("RGB")
            if not _blue_clusters(band):
                print(f"[{ts()}] [提示] 勾选框位置未见亮蓝边框——可能是对局内(选项蒙灰锁定)")
        except Exception:
            pass
    after = rd(rt(OFF_STORE), 1)
    after = after[0] if after else -1
    print(f"[{ts()}] [点击] ({pos[0]},{pos[1]}) 存储位 {before}->{after} "
          f"{'命中OK' if hit else '未命中(点偏?)'}")

    return hit


def _poll_label(first_delay, timeout):
    """轮询 OCR 直到 keys 任一关键词出现就立刻返回 (hits, 屏宽, 屏高)。
    常规用法等暴力行(=画面页就绪); 异页检测时也认 接受/取消/画面——
    对话框开着但无暴力行会立即返回(供调用方就地点画面设置页签), 不空等超时。"""
    time.sleep(first_delay)
    deadline = time.time() + timeout
    while True:
        lines, (W, H) = ocr_center()
        hits = _match_keys(lines, ("减少暴力表现", "暴力", "接受", "取消", "画面"))
        if hits:
            return hits, W, H    # 任一关键词命中即返回: 异页时接受/画面先出现,
                                 # 若只认暴力行会在这里空转整个超时(实测2s的根因)
        if time.time() >= deadline:
            return None
        time.sleep(0.15)


def _poll_menu_opt(first_delay, timeout):
    """按 ESC 后轮询菜单出现: 返回 ('选项'行, '返回游戏'行或None) / ('menu', None)
    (菜单在但没认出选项字样, 用标定坐标点) / (None, None)(没开出菜单)。
    若见到 接受/取消(=对话框还开着, 刚才的按键没生效) 返回 ('dialog', None),
    调用方据此重新分类, 绝不盲按 ESC 把对话框关掉。"""
    time.sleep(first_delay)
    deadline = time.time() + timeout
    while True:
        lines, _wh = ocr_center()
        hits = _match_keys(lines, ("选项", "返回游戏", "菜单", "接受", "取消"))
        if hits.get("选项"):
            return hits["选项"], hits.get("返回游戏")
        if hits.get("返回游戏") or hits.get("菜单"):
            return "menu", None
        if hits.get("接受") or hits.get("取消"):
            return "dialog", None
        if time.time() >= deadline:
            return None, None
        time.sleep(0.15)


def _in_match_hud():
    """对局检测: OCR 客户区右下角(62%宽~100% × 72%高~100%)找'菜单'按钮字样。
    对局内 HUD 右下角固定有'菜单'(主界面没有); 按钮配色随种族/装扮变,
    OCR 只认字不受影响。实测 1080p: 命中稳定, 单次 ~220ms。"""
    try:
        l, t, r, b = sc2_rect() or _primary_rect()
        w, h = r - l, b - t
        img = grab_screen(bbox=(l + int(w * 0.62), t + int(h * 0.72), r, b))
        img = img.resize((img.width * 2, img.height * 2))
        res = _ocr_recognize(img)
        for ln in res.get("lines", []):
            if "菜单" in _norm(ln.get("text", "")):
                return True
    except Exception:
        pass
    return False


def _ocr_left_band():
    """主裁剪区(30%~70%宽)比对话框窄且左移, 左列页签('画面设置'按钮, 在对话框
    顶部~10%高、x~27%处)落在裁剪线外会 OCR 不到。单独 OCR 客户区左带
    (16%~40%宽 × 5%~30%高——宽矮区域罩住对话框顶部页签列; 旧细高长条
    1:2.7 的形状 OCR 行检测失败=反复扫不出页签的根因)找含'画面'的行,
    返回行列表(全局坐标, 同 ocr_center)。实测单次 ~0.2s。"""
    try:
        l, t, r, b = sc2_rect() or _primary_rect()
        w, h = r - l, b - t
        x0, y0 = l + int(w * 0.16), t + int(h * 0.05)
        img = grab_screen(bbox=(x0, y0, l + int(w * 0.40), t + int(h * 0.30)))
        img = img.resize((img.width * 2, img.height * 2))
        res = _ocr_recognize(img)
        out = []
        for ln in res.get("lines", []):
            if "画面" not in _norm(ln.get("text", "")):
                continue
            brs = [wd["bounding_rect"] for wd in ln.get("words", []) if "bounding_rect" in wd]
            if not brs:
                continue
            xs = [b["x"] for b in brs]
            ys = [b["y"] for b in brs]
            xe = [b["x"] + b["width"] for b in brs]
            ye = [b["y"] + b["height"] for b in brs]
            out.append({"text": ln.get("text", ""),
                        "x": x0 + int(min(xs) / 2), "y": y0 + int(min(ys) / 2),
                        "w": max(1, int((max(xe) - min(xs)) / 2)),
                        "h": max(1, int((max(ye) - min(ys)) / 2))})
        return out
    except Exception:
        return []


class _SkipByDesign:
    """返回值哨兵: 工具**按规矩主动跳过**(检测到对局内、勾选框蒙灰锁定等),
    这不是故障。调用方必须 `if x is SKIPPED` 先判它、再判 None——两种情况对
    用户含义不同, 提示音也不同(跳过=短促一声, 失败=长低音)。用哨兵而不是
    模块级标志位: 标志位会被上一次调用的残留值串味(实测踩过)。"""
    def __bool__(self):
        return False
    def __repr__(self):
        return "<主动跳过>"


SKIPPED = _SkipByDesign()


def open_dialog(hwnd, attempts=3):
    """OCR 引导到达'选项对话框且暴力选项可见'。开局先识别: 对话框已开直接进
    定位, 菜单已开直接点'选项', 都没有(主界面)先按 F10 开菜单、没反应再按
    ESC——不盲按。
    判别按独有性排序: '暴力'行=对话框在画面页; '画面'=对话框页签(独有, 优先于
    '选项'——对话框标题也叫'选项'); '选项/菜单/返回游戏'=ESC菜单。
    所有等待都是轮询, 动画一就绪立即下一步。
    成功返回 (暴力行, 接受行或None, 屏宽, 屏高)——接受行与暴力行同出自一次
    OCR(任意分辨率都准), 供 click_toggle 提交用; 失败返回 None。
    返回 SKIPPED=检测到对局内主动跳过(不是故障), None=导航失败——两种情况对用户
    含义完全不同, 调用方要分开处理(提示音都不一样)。"""
    acted = False

    def ensure_act():
        """惰性前台化: OCR/探测不需要焦点, 第一次真正要按键/点击时才激活"""
        nonlocal acted
        if not acted:
            activate(hwnd)
            acted = True

    def _opt_then_settle(xy):
        """点'选项'开对话框并安置到画面页。
        轮询暴力行: 在画面页→成功返回; 开在别的页(有画面/接受而无暴力行)→
        就地点'画面设置'页签再轮询; 页签被主裁剪线截掉时用左带OCR补找。"""
        ensure_act()
        click(*xy)
        got = _poll_label(0.15, 1.8)
        h = got[0] if got else None
        if h:
            lab2 = h.get("减少暴力表现") or h.get("暴力")
            if lab2:
                return lab2, h.get("接受"), got[1], got[2]
        # 走到这=对话框已开但无暴力行(停在别的页): 找画面设置页签并点击
        ensure_act()
        tab = (h or {}).get("画面")
        if not tab:
            for cand in _ocr_left_band():
                if "画面" in _norm(cand["text"]):
                    tab = cand
                    break
        if not tab:
            return None                       # 页签也没认出 → 交给下一轮全量OCR
        click(*center(tab))
        got2 = _poll_label(0.15, 1.6)
        if got2:
            hh, w3, h3 = got2
            lab3 = hh.get("减少暴力表现") or hh.get("暴力")
            if lab3:
                return lab3, hh.get("接受"), w3, h3
        return None

    for attempt in range(1, attempts + 1):
        lines, (W, H) = ocr_center()
        hits = _match_keys(lines)
        lab = hits.get("减少暴力表现") or hits.get("暴力")
        tabs = [ln for ln in lines if "画面" in _norm(ln["text"])]
        if not tabs and (hits.get("接受") or hits.get("取消")):
            # 主裁剪区没'画面'但有对话框底钮(接受/取消)=对话框开着但页签列在裁剪区外
            tabs = _ocr_left_band()                # 单独OCR左带找'画面设置'页签
        tab = tabs[0] if tabs else None
        opt = hits.get("选项")
        menu = hits.get("返回游戏") or hits.get("菜单")
        where = ("画面页对话框" if lab else "对话框其他页" if tab else "ESC菜单" if opt else
                 "菜单(选项未认出)" if menu else "主界面/其他")
        print(f"[{ts()}] [导航] 第{attempt}轮 识别={where}")
        if lab:
            return lab, hits.get("接受"), W, H
        if tab:                                    # 对话框开着但不在画面页(记住的是上次停留页)
            ensure_act()
            tab = min(tabs, key=lambda ln: ln["x"])   # 页签固定在最左列; 画面页内容区有同名
            click(*center(tab))                    # '画面设置'小节标题, 取最左才是页签按钮
            got = _poll_label(0.15, 1.6)
            if got:
                lab2 = got[0].get("减少暴力表现") or got[0].get("暴力")
                if lab2:   # 页签没换页时 got 里只有 接受/取消(无暴力行), 必须丢弃重来——
                    return lab2, got[0].get("接受"), got[1], got[2]
            continue       # 否则 lab=None 传到 refine_checkbox 会直接崩(TypeError)
        if opt:                                    # ESC 菜单开着
            back = hits.get("返回游戏")
            if back and back["y"] < opt["y"]:
                # 对局内菜单布局: '返回游戏'在最上方; 主界面菜单里它在最底部。
                # 对局内选项被锁定(蒙灰可见但不可点), 点了存储位也不会翻转, 直接跳过。
                print(f"[{ts()}] [导航] 检测到对局内菜单('返回游戏'在'选项'上方=对局内布局), "
                      f"选项已锁定, 跳过自动点击。对局结束后再按。")
                return SKIPPED
            res2 = _opt_then_settle(center(opt))
            if res2:
                return res2
            continue
        if menu:
            res2 = _opt_then_settle(_frac_pt(*OPT_BTN_POS))
            if res2:
                return res2
            continue
        # 没有任何关键词: 先确认不是对局内(右下角HUD有'菜单'), 是就一个键都不按
        if _in_match_hud():
            print(f"[{ts()}] [导航] 检测到对局内HUD(右下角'菜单'按钮), 跳过自动流程, 不干扰对局。")
            return SKIPPED
        ensure_act()                               # 后面要发按键了, 先前台化
        # 先按 F10(通用菜单键), 轮询没开出菜单再按 ESC
        send_key(0x79)
        got, back = _poll_menu_opt(0.15, 1.2)
        if got is None:
            send_esc()
            got, back = _poll_menu_opt(0.15, 1.6)
        if got is None:                            # 没开出菜单(焦点/动画) → 重试
            continue
        if got == "dialog":
            continue                       # 对话框还开着(按键没生效) → 重新分类, 不按ESC
        if got != "menu" and back and back["y"] < got["y"]:
            send_esc()   # 刚才的按键开了对局暂停菜单, 再按一下恢复原状, 零点击
            print(f"[{ts()}] [导航] ESC后确认是对局内菜单('返回游戏'在'选项'上方), "
                  f"已按ESC恢复, 跳过自动点击。对局结束后再按。")
            return SKIPPED
        res2 = _opt_then_settle(center(got) if got != "menu" else _frac_pt(*OPT_BTN_POS))
        if res2:
            return res2
    return None


def close_dialog_and_menu():
    """全自动收尾: 此刻对话框开着、ESC菜单必在其后(对话框只能从菜单打开)。
    连按两次 ESC(间隔0.15s): 第一下关对话框, 第二下关菜单, 回到主界面。"""
    send_esc()
    time.sleep(0.15)
    send_esc()
    time.sleep(0.2)


# ---------------- 反和谐 + 自动切换和谐状态 ----------------
# 启动公告(源码内可自行编辑; 发布版默认展示)
UNCENSOR_MANUAL_TIP = ("内存部分完成! 请打开 选项>画面设置, 手动 勾选→取消勾选 "
                       "'减少暴力表现' 各一次(点击才触发游戏生效)。")


def uncensor():
    """手动模式: 完成内存部分, 用户去选项里点击一次"""
    pm, base = get_pm()
    if pm is None:
        return fail(f"[反和谐] {_no_game_msg()}")
    if not apply_memory_recipe():
        return fail("[反和谐] 内存写入未生效(可能被杀软/权限拦截), 已中止")
    print(f"[{ts()}] [反和谐·手动] {UNCENSOR_MANUAL_TIP}")
    beep(True, "open")
    return 0


def uncensor_auto():
    """全自动模式: 内存部分 + 自动开对话框 + 双击勾选框(验证) + 关闭"""
    pm, base = get_pm()
    if pm is None:
        return fail(f"[全自动] {_no_game_msg()}")
    print(f"[{ts()}] [全自动] 步骤1/3: 内存配方...")
    if not apply_memory_recipe():
        return fail("[全自动] 内存写入未生效(可能被杀软/权限拦截), 已中止")
    hwnd = find_sc2_hwnd()
    if hwnd is None:
        return fail("[全自动] 找不到 SC2 窗口")
    print(f"[{ts()}] [全自动] 步骤2/3: OCR 导航打开选项对话框...")
    res = open_dialog(hwnd)
    if res is SKIPPED:         # 对局内主动跳过: 不是故障, 用短促提示音
        return skip("[全自动] 对局内已跳过(选项锁定, 不点击), 不干扰对局。对局结束后再按。")
    if res is None:
        return fail("[全自动] 未到达选项对话框, 可改用手动模式(uncensor 键)")
    activate(hwnd)                     # 点击需要前台(导航过程已激活过则立即返回)
    why = _click_gate()
    if why:
        return fail(f"[全自动] 不能点击: {why}。全屏模式请把游戏改成窗口化/无边框"
                    f"(或改用手动模式 F8 自己进选项点一次)")
    print(f"[{ts()}] [全自动] 步骤3/3: 点击 勾选→取消...")
    pos = refine_checkbox(res[0], res[2], res[3])   # 定位一次
    if pos is SKIPPED:      # 勾选框蒙灰锁定(多在对局内): 主动跳过, 短促音
        return skip("[全自动] 勾选框蒙灰(选项锁定, 多在对局内), 未点击。对局结束后再按。")
    if pos is None:
        return fail("[全自动] 勾选框定位失败, 未点击。")
    if not click_checkbox_verified(pos):
        return fail("[全自动] 第一次点击未命中, 可重按全自动键重试")
    if not click_checkbox_verified(pos):            # 对话框不动, 复用坐标
        return fail("[全自动] 第二次点击未命中")
    close_dialog_and_menu()
    print(f"[{ts()}] [全自动] 完成! 之后游戏会反和谐, "
          f"少部分战役场景模型跟随简中语音包依然和谐。")
    beep(True, "open")
    return 0


def click_toggle():
    """自动切换和谐状态: 自动开对话框 → 点击一次(验证) → 点'接受'提交 →
    0.15s 后 ESC 关掉身下的菜单, 回主界面。
    注意必须点'接受'提交: 实测 ESC 关对话框会回滚未提交的改动(全自动模式没
    这个问题, 是因为它点两次回到原值)。'接受'行取自 open_dialog 的同一次
    OCR(任意分辨率/比例都准, 不额外耗时), OCR 漏识别时才退标定坐标兜底。"""
    pm, base = get_pm()
    if pm is None:
        return fail(f"[切换] {_no_game_msg()}")
    nop, _s, _r = read_state()
    if nop != "nop":
        print(f"[{ts()}] [切换] NOP 未打, 先打 NOP...")
        if not ensure_nop():
            return fail("[切换] NOP 写入未通过校验, 已中止")
    hwnd = find_sc2_hwnd()
    if hwnd is None:
        return fail("[切换] 找不到 SC2 窗口")
    res = open_dialog(hwnd)
    if res is SKIPPED:
        return skip("[切换] 对局内已跳过(选项锁定, 不点击), 不干扰对局。对局结束后再按。")
    if res is None:
        return fail("[切换] 未到达选项对话框")
    activate(hwnd)                     # 点击需要前台(导航过程已激活过则立即返回)
    why = _click_gate()
    if why:
        return fail(f"[切换] 不能点击: {why}。全屏模式请把游戏改成窗口化/无边框")
    lab, acc, W, H = res
    pos = refine_checkbox(lab, W, H)
    if pos is SKIPPED:
        return skip("[切换] 勾选框蒙灰(选项锁定, 多在对局内), 未点击。对局结束后再按。")
    if pos is None:
        return fail("[切换] 勾选框定位失败, 未点击。")
    if not click_checkbox_verified(pos):
        return fail("[切换] 点击未命中(点偏了?), 可再按一次重试")
    if acc:                                  # 接受行来自同一次OCR, 任意分辨率都准
        click(*center(acc))
        print(f"[{ts()}] [提交] 点'接受' ({acc['x']},{acc['y']})")
    else:                                    # OCR 没认出时才用标定坐标兜底
        click(*_frac_pt(0.4336, 0.9278))
        print(f"[{ts()}] [提交] OCR未认出'接受', 标定坐标提交")
    time.sleep(0.15)                         # 对话框关闭
    send_esc()                               # 关掉身后的 ESC 菜单
    time.sleep(0.15)
    nop, store, r = read_state()
    beep(True, "open" if store == 0 else "close")
    print(f"[{ts()}] [切换] 切换完成并提交(存储位={store}, "
          f"{'反和谐' if store == 0 else '和谐'}), 已回主界面。再按一次切回。")
    return 0


def uncensor_off():
    """关闭反和谐·手动: 恢复内存原状, 用户手动点一次选项生效"""
    pm, base = get_pm()
    if pm is None:
        return fail(f"[恢复手动] {_no_game_msg()}")
    if not restore_memory_recipe():
        return fail("[恢复手动] 内存恢复未通过校验, 已中止")
    print(f"[{ts()}] [恢复手动] 内存已恢复! 请到 选项>画面设置 手动点一下"
          f"\"减少暴力表现\"(点完和谐生效)。")
    beep(True, "close")
    return 0


def uncensor_off_auto():
    """关闭反和谐·自动: 恢复内存原状 → 自动开对话框点一次(验证) → 点'接受'
    提交 → ESC 关菜单回主界面。流程与 F9 相同, 只是内存部分换成恢复。"""
    pm, base = get_pm()
    if pm is None:
        return fail(f"[恢复自动] {_no_game_msg()}")
    if not restore_memory_recipe():
        return fail("[恢复自动] 内存恢复未通过校验, 已中止")
    hwnd = find_sc2_hwnd()
    if hwnd is None:
        return fail("[恢复自动] 找不到 SC2 窗口")
    print(f"[{ts()}] [恢复自动] OCR 导航打开选项对话框...")
    res = open_dialog(hwnd)
    if res is SKIPPED:
        return skip("[恢复自动] 对局内已跳过(选项锁定, 不点击), 不干扰对局。对局结束后再按。")
    if res is None:
        return fail("[恢复自动] 未到达选项对话框, 可改用手动恢复(Ctrl+Alt+3)")
    activate(hwnd)                     # 点击需要前台(导航过程已激活过则立即返回)
    why = _click_gate()
    if why:
        return fail(f"[恢复自动] 不能点击: {why}。全屏模式请把游戏改成窗口化/无边框")
    lab, acc, W, H = res
    pos = refine_checkbox(lab, W, H)
    if pos is SKIPPED:
        return skip("[恢复自动] 勾选框蒙灰(选项锁定, 多在对局内), 未点击。对局结束后再按。")
    if pos is None:
        return fail("[恢复自动] 勾选框定位失败, 未点击。")
    if not click_checkbox_verified(pos):
        return fail("[恢复自动] 点击未命中, 可重按恢复键重试")
    if acc:                                  # 接受行来自同一次OCR, 任意分辨率都准
        click(*center(acc))
        print(f"[{ts()}] [提交] 点'接受' ({acc['x']},{acc['y']})")
    else:                                    # OCR 没认出时才用标定坐标兜底
        click(*_frac_pt(0.4336, 0.9278))
        print(f"[{ts()}] [提交] OCR未认出'接受', 标定坐标提交")
    time.sleep(0.15)                         # 对话框关闭
    send_esc()                               # 关掉身后的 ESC 菜单
    time.sleep(0.15)
    nop, store, r = read_state()
    print(f"[{ts()}] [恢复自动] 完成! 已恢复和谐并提交(存储位={store}), 已回主界面。")
    beep(True, "close")
    return 0


# ---------------- 主循环 ----------------
def build_defs():
    """config.hotkeys → [(id, mods, vk, 名称)]"""
    defs, hid = [], 10
    for name in ["hotkey_master", "uncensor", "uncensor_auto", "click_toggle", "status",
                 "restore", "restore_auto"]:
        key = CFG["hotkeys"].get(name)
        mods, vk = parse_key(key) if key else (None, None)
        if vk is None:
            print(f"[警告] 快捷键 {name}={key} 无法识别, 跳过")
            continue
        defs.append((hid, mods, vk, name))
        hid += 1
    return defs


def _defs_with_keys(defs):
    """build_defs 的 4 元组 → 界面契约要的 5 元组(补上配置里的键字符串)。
    _ALL_DEFS 用这个形状: 界面要靠它显示"暂停但未注册"的快捷键键位。"""
    return [(i, m, v, n, str(CFG["hotkeys"].get(n, ""))) for i, m, v, n in defs]


def _forward_hotkey(vk):
    """按键转发: 注销该快捷键 → 合成一次**带扫描码**的真实按键 → 重新注册。

    ⚠️ 必须由注册快捷键的那个线程调用(跨线程注销实测无效, err=1419), 所以只有
    主循环的 WM_APP_FORWARD 分支和 maybe_forward_key 的兜底线程调它。
    它是转发路径的**唯一实现**: 以前主循环里内联了一份、maybe_forward_key 的异常
    兜底又只有一句"重注册"——出了错没人知道, 也可能把键留成注销态(按键从此失灵)。

    注册动作全部检查返回值: 重注册失败=这个快捷键从此按下没反应, 必须**写日志**
    (控制台版/界面日志面板都能看到), 而不是静默失效。正常路径不重试(本线程里
    失败通常要下个消息循环才有机会恢复, 重试也是白搭); 兜底路径重试一次。
    """
    ident = mods = None
    for i, m, v, _n, _k in _REG_OK:
        if v == vk:
            ident, mods = i, m
            break
    if ident is None:
        return
    scan = VK_SCAN.get(vk)
    if scan is None:
        print(f"[{ts()}] [转发] 0x{vk:02X} 没有对应扫描码, 跳过转发(键已正常注册)")
        return
    scan, ext = scan
    ex = KEYEVENTF_EXTENDEDKEY if ext else 0
    backed = False
    try:
        backed = bool(user32.UnregisterHotKey(None, ident))   # 返回布尔, 不成功=没注销
    except Exception as e:
        print(f"[{ts()}] [转发] 注销快捷键失败({e}), 不转发(按键不受影响)")
        return
    if not backed:
        print(f"[{ts()}] [转发] 注销 0x{vk:02X} 未成功(可能已被别的程序接管), 不转发")
        return
    back = False
    try:
        time.sleep(0.05)
        user32.keybd_event(0, scan, KEYEVENTF_SCANCODE | ex, 0)
        time.sleep(0.06)
        user32.keybd_event(0, scan, KEYEVENTF_SCANCODE | ex | KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)
    finally:
        # 无论转发本身成没成, 都要把快捷键注册回去 —— 否则这个功能键从此失灵
        for attempt in (1, 2):
            try:
                if user32.RegisterHotKey(None, ident, mods, vk):
                    back = True
                    break
            except Exception:
                pass
            if attempt == 1:
                time.sleep(0.3)
    if back:
        print(f"[{ts()}] [转发] 已把按键(扫描码0x{scan:02X})转发给游戏")
    else:
        # 按键确实转发了, 但快捷键没注册回来 —— 如实说明, 别让用户以为是键坏了
        print(f"[{ts()}] [转发] [错误] 按键已转发, 但 {vk:#04x} 重注册两次都失败: "
              f"该快捷键已失效, 请改一下键位或重启工具")


def _single_instance_check():
    """单实例互斥: 已有本工具在运行时明确报错(旧窗口占着快捷键=新键全失灵的元凶)"""
    ERROR_ALREADY_EXISTS = 183
    kernel32.CreateMutexW(None, False, "SC2Uncensor_SingleInstance_Mutex")
    if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
        print("[错误] 本工具已在运行(可能开着旧的工具窗口)!")
        print("       快捷键是全局排他的: 旧窗口不关, 新窗口的键全部无效。")
        print("       请关闭所有旧的工具控制台窗口(标题 SC2 Test Hotkeys / SC2 Uncensor)后再开。")
        beep(False, "fail")
        return False
    return True


def register_hotkeys_with_fallback(defs):
    """注册快捷键; 失败时自动尝试备用键(Ctrl+Alt+原键), 返回 (成功列表, 失败列表)"""
    ok, failed = [], []
    for _d in defs:
        ident, mods, vk, name = _d[:4]     # 兼容 4 元组(build_defs)与 5 元组(_ALL_DEFS)
        key = str(CFG["hotkeys"][name])   # 防配置写成数字/其他类型
        if user32.RegisterHotKey(None, ident, mods, vk):
            ok.append((ident, mods, vk, name, key))
            continue
        # 备用1: Ctrl+Alt+原键
        bk_mods = MOD_CONTROL | MOD_ALT | (mods & MOD_NOREPEAT)
        if user32.RegisterHotKey(None, ident, bk_mods, vk):
            alt = ("Ctrl+Alt+" + key.replace("Ctrl+", "").replace("Alt+", "").replace("Shift+", ""))
            ok.append((ident, bk_mods, vk, name, alt))
            print(f"[提示] 快捷键 {key} 被占用, {name} 已自动改用备用键 {alt}")
            continue
        # 备用2: Ctrl+Shift+原键
        bk_mods2 = MOD_CONTROL | MOD_SHIFT | (mods & MOD_NOREPEAT)
        if user32.RegisterHotKey(None, ident, bk_mods2, vk):
            alt = "Ctrl+Shift+" + key.replace("Ctrl+", "").replace("Alt+", "").replace("Shift+", "")
            ok.append((ident, bk_mods2, vk, name, alt))
            print(f"[提示] 快捷键 {key} 被占用, {name} 已自动改用备用键 {alt}")
            continue
        failed.append((name, key))
    return ok, failed


def _require_hotkey_thread(what):
    """这些操作只能由注册快捷键的线程做(跨线程注销实测无效), 误调时给明确提示。"""
    if _MAIN_TID is None or kernel32.GetCurrentThreadId() != _MAIN_TID:
        raise RuntimeError(f"{what} 必须在快捷键线程执行: 请用 post_ui_call({what}) 投递")


def set_hotkeys_enabled(flag):
    """**幂等**设置"其他快捷键"的启用状态(总开关 hotkey_master 自身始终保留)。

    为什么需要幂等版: 界面"捕获新快捷键"时必须先确保处于暂停态(否则用户想把某功能设成
    F9 时, 一按 F9 会先把当前的 F9 功能执行一遍), 捕获完再确保恢复; 而原来的 toggle 是
    翻转语义, 连调两次就错位, UI 没法安全使用。

    必须在快捷键线程执行(用 post_ui_call)。返回 (本次注册数, 本次注销数)。
    """
    global _hotkeys_enabled, _REG_OK, _KEY_BY_NAME, _REG_BY_NAME
    _require_hotkey_thread("set_hotkeys_enabled")
    flag = bool(flag)
    if flag == _hotkeys_enabled:
        return 0, 0                     # 已经是这个状态: 幂等, 什么都不做
    regs = un = 0
    if flag:
        # 恢复: 只补注册"其他键"(总开关一直没注销, 再注册一次会把自己挤去备用键)
        others = [d for d in _ALL_DEFS if d[3] != "hotkey_master"]
        ok, failed = register_hotkeys_with_fallback(others)
        keep = [r for r in _REG_OK if r[3] == "hotkey_master"]
        _REG_OK = keep + ok
        regs = len(ok)
        _hotkeys_enabled = True
        tail = f"; {len(failed)} 个连同备用键都没注册上" if failed else ""
        print(f"[{ts()}] [总开关] 其他快捷键已恢复{tail}")
        beep(True, "on")          # 单音(见 _beep_play 的说明): 一按只响一声
    else:
        keep = []
        for ident, mods, vk, name, key in _REG_OK:
            if name == "hotkey_master":
                keep.append((ident, mods, vk, name, key))
                continue
            if user32.UnregisterHotKey(None, ident):
                un += 1
        _REG_OK = keep
        _hotkeys_enabled = False
        print(f"[{ts()}] [总开关] 已暂停 {un} 个快捷键(按键返还系统, 其他软件正常使用); "
              f"总开关 {_KEY_BY_NAME.get('hotkey_master', '总开关')} 仍有效")
        beep(True, "off")         # 单音低: 与"恢复"同一个音高方向, 但只有一声
    _KEY_BY_NAME = {n: k for _i, _m, _v, n, k in _REG_OK}
    _REG_BY_NAME = {n: (i, m, v) for i, m, v, n, _k in _REG_OK}
    return regs, un


def reload_hotkeys():
    """按当前 CFG["hotkeys"] 重新注册全部快捷键(注销旧的 → 重建 → 注册, 含备用键逻辑)。
    用户改完 config.json 调它即可立刻生效, 不必重启工具。

    必须在快捷键线程执行(用 post_ui_call)。返回 (生效表, 失败表), 元素 (name, 键字符串);
    生效表里的键是**实际生效**的键(被占用自动换备用键时给的就是备用键, 便于界面如实显示)。
    重载不改变"_hotkeys_enabled"; 处于暂停态时只注册总开关, 其余只更新定义不注册
    (绝不因为一次重载把用户暂停的快捷键偷偷打开)。
    """
    global _ALL_DEFS, _REG_OK, _NAME_BY_ID, _KEY_BY_NAME, _REG_BY_NAME
    _require_hotkey_thread("reload_hotkeys")
    _ALL_DEFS = _defs_with_keys(build_defs())      # 界面契约: 5 元组(含键字符串)
    for ident, _m, _v, _n, _k in _REG_OK:         # 旧的全部注销干净(残留=键位幽灵占用)
        user32.UnregisterHotKey(None, ident)
    _REG_OK, _KEY_BY_NAME, _REG_BY_NAME = [], {}, {}
    _NAME_BY_ID = {i: n for i, _m, _v, n, _k in _ALL_DEFS}
    want = (_ALL_DEFS if _hotkeys_enabled
            else [d for d in _ALL_DEFS if d[3] == "hotkey_master"])
    ok, failed = register_hotkeys_with_fallback(want)
    _REG_OK = ok
    _KEY_BY_NAME = {n: k for _i, _m, _v, n, k in ok}
    _REG_BY_NAME = {n: (i, m, v) for i, m, v, n, _k in ok}
    print(f"[{ts()}] [快捷键] 已按配置重载: 生效 {len(ok)} 个"
          + (f", 失败 {len(failed)} 个" if failed else "")
          + ("" if _hotkeys_enabled else " (总开关处于暂停态: 只保留总开关)"))
    return [(n, k) for _i, _m, _v, n, k in ok], list(failed)


class _UiCall:
    """界面的一次请求: 在快捷键线程执行 fn, 结果/异常带回调用方线程"""

    def __init__(self, fn, label=None):
        self.fn = fn
        self.label = label
        self.done = threading.Event()
        self.result = None
        self.exc = None
        self.cancelled = False


def post_ui_call(fn, label=None, timeout=None):
    """把 fn 投递到快捷键线程执行(与快捷键操作天然串行), 返回 fn 的返回值。

    - fn 执行期间自动套"一次操作只响一次"的边界, 声音由流程函数自己负责, 本通道不发声。
    - fn 的异常: 记日志, 并在调用方线程重新抛出同一个异常对象(工具不会因此退出)。
    - timeout: None=一直等; 超时抛 TimeoutError。**超时≠取消**——请求仍在队列里,
      忙完照样会执行(所以只有只读轮询适合用小 timeout, 动作类请用 None)。
    - 快捷键线程没在跑(单实例冲突/main 未启动/已退出)时抛 RuntimeError。
    """
    tid = _MAIN_TID
    if tid is None:
        raise RuntimeError("快捷键线程未运行: 可能已有本工具在运行, 或还没启动核心主循环")
    if _MAIN_DONE.is_set():
        raise RuntimeError("快捷键线程已退出"
                           + ("(单实例冲突: 已有本工具在运行)"
                              if _MAIN_FAILED == "single_instance" else ""))
    if kernel32.GetCurrentThreadId() == tid:
        return fn()                     # 已在快捷键线程内: 直接执行, 免得自己等自己(死锁)
    c = _UiCall(fn, label)
    _UI_CALLS.put(c)
    if not user32.PostThreadMessageW(tid, WM_APP_UI_CALL, 0, 0):
        c.cancelled = True              # 投递失败(队列没建/线程已退): 作废, 别让调用方干等
        raise RuntimeError(f"投递到快捷键线程失败(err={ctypes.GetLastError()}): 快捷键线程可能没在跑")
    if not c.done.wait(timeout):
        raise TimeoutError(f"快捷键线程 {timeout}s 内没执行完(可能正在跑一次流程); "
                           f"注意该请求仍会在忙完后执行")
    if c.exc is not None:
        raise c.exc
    return c.result


# ---------------- 按键转发(快捷键拦截补偿) ----------------
# 全局快捷键会吞掉按键(游戏收不到)。转发 = 后台线程里: 前台确认后投递回主线程,
# 主线程: 临时注销该快捷键 → 向前台的游戏合成一次带扫描码的真实按键 → 重新注册。
# 默认键(F8/F9/F11, 特意选了主界面无功能的键)只在对局内转发; 用户改成非默认
# 键(可能撞上主界面快捷键)则只要星际在前台就转发。仅单键无修饰; 完全异步。
# ---- 快捷键运行状态(模块级: 界面/重载/暂停恢复都要读写, 不能再藏在 main() 局部) ----
_REG_OK = []        # [(ident, mods, vk, name, key)] 当前"确实注册着"的, key=实际生效键
_ALL_DEFS = []      # [(ident, mods, vk, name, key)] 按 CFG 解析出的全部定义(含暂停未注册的)
_NAME_BY_ID = {}    # ident -> name        (快捷键分发用)
_KEY_BY_NAME = {}   # name -> 生效键字符串  (界面显示真实键位用)
_REG_BY_NAME = {}   # name -> (ident, mods, vk)   转发用
_hotkeys_enabled = True   # 快捷键总开关(False=其他快捷键已注销暂停)

# ---- 界面(UI)对接通道 ----
# UI 有自己的事件循环, 但 RegisterHotKey/UnregisterHotKey 只能由注册快捷键的那个线程
# 调用(实测: 跨线程注销返回 0 且 err=1419, 无效), 而且点击类流程必须与快捷键操作串行
# (否则两个 OCR+注入点击流程并发会互抢前台、拿错坐标去点)。所以 UI 的一切核心调用
# 都通过 post_ui_call 投递到快捷键线程执行。
WM_APP_UI_CALL = 0x8000 + 2         # 与 WM_APP_FORWARD 并列
_UI_CALLS = queue.Queue()           # 元素: _UiCall
_HOTKEY_READY = threading.Event()   # main() 注册完快捷键后 set(); UI 等它
_MAIN_DONE = threading.Event()      # main() 已退出(含失败返回)
_MAIN_FAILED = None                 # main() 失败原因: None / "single_instance"
_MAIN_TID = None                    # 快捷键线程 id(main() 第一行设置; 原来只在 main 内
                                    # global 赋值, 模块级没定义, post_ui_call 早读会 NameError)


def maybe_forward_key(name):
    """快捷键触发时调用: 异步把被拦截的按键转发给前台的游戏。
    关键: 必须带扫描码(游戏的原生输入层按扫描码识别, scancode=0 会被忽略)"""
    if not CFG.get("forward_keys", True):
        return
    reg = _REG_BY_NAME.get(name)
    if not reg:
        return
    _ident, mods, vk = reg
    real_mods = mods & (MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN)
    if real_mods or vk not in VK_SCAN:
        return   # 仅转发"无修饰键的单键"; 组合键/鼠标键/Pause 不做(MOD_NOREPEAT 是附加标志, 不算)

    def worker():
        try:
            hwnd = find_sc2_hwnd()
            if not hwnd or user32.GetForegroundWindow() != hwnd:
                return   # 游戏不在前台: 按键本来也不会到游戏, 不转发
            # 注销/发送/重注册必须在注册线程(主线程)执行——跨线程注销会静默失败
            if not user32.PostThreadMessageW(_MAIN_TID, WM_APP_FORWARD, vk, 0):
                # 主线程退出/消息队列没了: 这条转发没人做, 明说一句(以前是静默吞掉)
                print(f"[{ts()}] [转发] 投递给快捷键线程失败(err={ctypes.GetLastError()}), "
                      f"本次按键未转发给游戏")
        except Exception as e:
            # 兜底: 走到这多半是投递路径出了意外 —— 快捷键此刻仍是注册着的(注销发生在
            # 主线程里), 所以这里**不该**再补一次注册(重复注册=幽灵注册, 见经验 16③),
            # 只把原因写进日志。
            print(f"[{ts()}] [转发] 转发线程异常({e}), 本次按键未转发(快捷键本身不受影响)")
    threading.Thread(target=worker, daemon=True).start()


def toggle_hotkeys_master(ok=None):
    """快捷键总开关(默认快捷键 Ctrl+Alt+1): 翻转"其他快捷键"的启用状态, 总开关自身始终有效。
    参数 ok 仅为兼容旧调用, 已不使用(状态在模块级); 内部复用 set_hotkeys_enabled,
    避免出现两套暂停逻辑。"""
    return set_hotkeys_enabled(not _hotkeys_enabled)


def main(register_hotkeys=True):
    """快捷键主循环(必须在"要注册快捷键的那个线程"里跑)。

    register_hotkeys=False 供界面/自动化测试用: 照常建消息队列、派发请求、发就绪信号,
    但不真正抢占用户的按键(测 post_ui_call / 重载 / 暂停等语义时用)。
    """
    global _MAIN_TID, _MAIN_FAILED, _ALL_DEFS, _REG_OK, _KEY_BY_NAME, _REG_BY_NAME, _NAME_BY_ID
    _MAIN_TID = kernel32.GetCurrentThreadId()
    _MAIN_FAILED = None
    _MAIN_DONE.clear()
    # 先强制创建本线程的消息队列: 否则界面在 GetMessageW 第一次被调用之前
    # PostThreadMessageW 会失败(err=1444 ERROR_INVALID_THREAD_ID), 请求静默丢失。
    _qmsg = wt.MSG()
    user32.PeekMessageW(ctypes.byref(_qmsg), None, 0, 0, 0)
    try:
        if not _single_instance_check():
            _MAIN_FAILED = "single_instance"
            return 2
        threading.Thread(target=_watchdog, daemon=True).start()   # 卡死时留证据用
        defs = build_defs()
        _ALL_DEFS = _defs_with_keys(defs)      # 界面契约: 5 元组(含键字符串)
        _NAME_BY_ID = {ident: name for ident, _m, _v, name in defs}
        if register_hotkeys:
            ok, failed = register_hotkeys_with_fallback(defs)
        else:
            ok, failed = [], []      # 测试用: 不抢用户按键(快捷键表为空, 派发也不会触发)
        _REG_OK = ok
        _KEY_BY_NAME = {name: key for _i, _m, _v, name, key in ok}
        _REG_BY_NAME = {name: (ident, mods, vk) for ident, mods, vk, name, _k in ok}
        if failed:
            print(f"[错误] 以下快捷键连同备用键全部注册失败: "
                  f"{', '.join(f'{n}={k}' for n, k in failed)}")
            print("       → 大概率有旧工具窗口/其他软件占用, 关闭后重启本工具")
        print(__doc__)
        print("当前快捷键: " + ", ".join(f"{_KEY_BY_NAME[n]}={n}" for _i, _m, _v, n, _k in ok))
        print(f"[{ts()}] 就绪(游戏可稍后启动)。")
        if failed:
            beep(False, "fail")
        _HOTKEY_READY.set()          # 界面等这个信号(上面失败返回时不会 set)

        msg = wt.MSG()
        lp = ctypes.byref(msg)
        while user32.GetMessageW(lp, None, 0, 0) > 0:
            if msg.message == WM_APP_FORWARD:
                # 转发请求(转发线程投递): 必须在注册线程执行——注销快捷键→
                # 合成带扫描码的按键→重注册(跨线程注销实测无效, err=1419)
                try:
                    vk = msg.wParam
                    reg = next((r for r in _REG_OK if r[2] == vk), None)
                    if reg:
                        _i, _m, _v, name, key = reg
                        # 默认键特意选了主界面无功能的F键: 只在对局内转发;
                        # 非默认键(可能撞主界面快捷键): 星际在前台就转发, 不分对局内外
                        is_default = (key == DEFAULT_CONFIG["hotkeys"].get(name, key))
                        if _in_match_hud() or not is_default:
                            _forward_hotkey(vk)
                    else:
                        # 门槛里的竞态: 转发线程投递后、主循环处理前, 用户刚好按了总开关
                        # 把这一批快捷键注销了 —— 这时不转发(键已不在我们手里)
                        print(f"[{ts()}] [转发] 0x{vk:02X} 不在已注册表里"
                              f"(可能刚被暂停/注销), 跳过")
                except Exception:
                    import traceback
                    print(f"[{ts()}] [转发] 异常: " + traceback.format_exc())
            elif msg.message == WM_APP_UI_CALL:
                # 界面请求: 一条消息把队列里积压的请求全部做完(消息数与请求数不匹配时
                # 也不会有请求没人取)。每个请求各自套一次"操作边界", 于是它的声音
                # (一次操作只响一次)与看门狗计时都按这一次请求算。
                while True:
                    try:
                        c = _UI_CALLS.get_nowait()
                    except queue.Empty:
                        break
                    if c.cancelled:
                        continue
                    if c.label:
                        print(f"[{ts()}] [界面] {c.label}")
                    _beep_op_begin()
                    try:
                        c.result = c.fn()
                    except Exception:
                        import traceback
                        print(f"[{ts()}] [错误] 界面请求异常(工具继续运行):")
                        print(traceback.format_exc())
                        c.exc = sys.exc_info()[1]
                    finally:
                        _beep_op_end()
                        c.done.set()
            elif msg.message == WM_HOTKEY:
                name = _NAME_BY_ID.get(msg.wParam)
                # 收到按键立刻记一行: 以后"按了没反应"可直接看日志——没有这一行
                # 就说明消息循环卡住了(如控制台被划选), 有这行才轮到看后面流程
                print(f"[{ts()}] [按键] {name or '未知键'}")
                _beep_op_begin()   # 本次按键的声音统一在收尾响一次(不重复响)
                try:
                    # 任何快捷键流程(含总开关/按键转发)抛异常都只记日志不退出——
                    # 工具死掉=快捷键全失灵(项目经验第8条)
                    try:
                        if name == "hotkey_master":
                            toggle_hotkeys_master()   # 总开关: 自身始终有效
                        elif not _hotkeys_enabled:
                            pass   # 总开关已暂停其他快捷键: 忽略(键也已注销)
                        else:
                            maybe_forward_key(name)   # 异步转发被拦截的按键给游戏(不阻塞)
                        if name != "hotkey_master" and not _hotkeys_enabled:
                            print(f"[{ts()}] [总开关] {name} 已暂停, 忽略")
                            continue   # 只忽略这一次(写 break 会把整个工具退出=快捷键全灭)
                        if name == "uncensor":
                            uncensor()
                        elif name == "uncensor_auto":
                            uncensor_auto()
                        elif name == "click_toggle":
                            click_toggle()
                        elif name == "restore":
                            uncensor_off()
                        elif name == "restore_auto":
                            uncensor_off_auto()
                        elif name == "status":
                            print_status()
                        elif name == "quit":
                            print(f"[{ts()}] 退出")
                            break
                    except Exception:
                        import traceback
                        print(f"[{ts()}] [错误] {name} 流程异常(工具继续运行):")
                        print(traceback.format_exc())
                        beep(False, "fail")
                finally:
                    _beep_op_end()
        for ident, _m, _v, _n, _k in _REG_OK:
            user32.UnregisterHotKey(None, ident)
        return 0
    finally:
        _MAIN_DONE.set()             # 界面据此知道核心已退出(别再来投递了)


if __name__ == "__main__":
    # 直接跑本文件 = 控制台版(sc2_uncensor.bat)。**这个入口曾经丢失过**(被误缩进到
    # main() 里成了死代码): 结果 `python sc2_uncensor.py` 只打横幅就退出, 控制台版
    # 等于完全不能用(界面版走 import, 所以没被发现)。勿删, 也勿缩进进 main()。
    #
    # SC2_UNCENSOR_NO_HOTKEYS=1: 照常起消息循环但不真注册全局快捷键 —— 回归测试与打包
    # 后的冒烟靠它验"这个入口还能起来", 又不会去抢用户(或构建机主人)的 F8/F9/F11。
    sys.exit(main(register_hotkeys=not os.environ.get("SC2_UNCENSOR_NO_HOTKEYS")))
