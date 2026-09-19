# -*- coding: utf-8 -*-
"""
UI 主题层：皮肤素材加载、DPI 缩放、字体、以及几个自绘控件。

这一层**完全不接触核心逻辑**（不 import sc2_uncensor），可以单独跑、单独改。
美术素材由 scripts/gen_ui_skin.py 生成到 app/ui_skin/：缺文件时所有控件自动
退回纯色深色样式，界面仍然可用——所以换肤/换图不会把程序搞崩。
"""
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

# ---------------- 窗口几何（必须与 scripts/gen_ui_skin.py 顶部一致） ----------------
WINDOW_W, WINDOW_H = 700, 780
FRAME_INSET = (26, 42, 26, 30)      # 左/上/右/下 边框厚度: 内容区从这里开始
CONTENT_X0 = FRAME_INSET[0] + 14
CONTENT_Y0 = FRAME_INSET[1] + 10
CONTENT_W = WINDOW_W - FRAME_INSET[0] - FRAME_INSET[2] - 28
# 按钮族: (逻辑宽, 逻辑高)。primary=主按钮 hero/手动·全自动两级  big=240宽的两列排布
#                           wide=160 small=88(单行) mini=64(小工具按钮)
BTN_SIZES = {"primary": (300, 52), "hero": (240, 58), "big": (240, 48),
             "btn": (145, 46), "wide": (160, 46), "small": (88, 34), "mini": (64, 32)}

# 字号整体放大系数：中文界面 9px 在 125% 缩放下偏小(实测用户反馈"看得费劲")，
# 统一在这里放大，别去逐个调用点改数字。
FONT_BOOST = 1.4

# ---------------- 配色（与 gen_ui_skin.py 的 P 保持一致） ----------------
_P = {
    "metal_dark":  (22, 28, 31),
    "metal":       (40, 49, 54),
    "metal_light": (66, 78, 84),
    "edge_hi":     (118, 136, 144),
    "neon":        (53, 224, 200),
    "neon2":       (79, 168, 255),
    "bg":          (14, 18, 20),
    "panel":       (23, 29, 32),
    "text":        (222, 232, 235),
    "text_dim":    (138, 154, 160),
    "text_soft":   (196, 208, 213),   # 比 text 暗、比 text_dim 亮: 说明性正文用它(15px 下 text_dim 偏灰)
    "warn_soft":   (226, 174, 84),    # 强调用的暖色, 比 warn 稳一点(橙字多了不扎眼)
    "good":        (46, 214, 140),
    "warn":        (240, 176, 48),
    "bad":         (236, 84, 84),
    "off":         (86, 98, 104),
}


def _hx(rgb):
    return "#%02x%02x%02x" % rgb


C = {k: _hx(v) for k, v in _P.items()}
# 聚焦提示那圈"框外像素"的颜色: 比框色(neon)淡一点点。派生出来的, 不进 _P
# (gen_ui_skin.py 的调色板要跟 _P 保持一致, 派生色不用同步)
C["neon_pale"] = _hx(tuple(int(round(_P["neon"][i] + (_P["text"][i] - _P["neon"][i]) * 0.32))
                       for i in range(3)))
C["dim"] = "#0b0e10"          # 禁用按钮上的暗遮罩
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"


def dpi_scale(root):
    """显示缩放(1.0/1.25/1.5...)。核心 import 时会调 SetProcessDPIAware，
    所以这里拿到的就是物理像素下的真实 DPI。"""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        dpi = 96.0
    if dpi <= 0:
        dpi = 96.0
    s = round(dpi / 96.0 * 4) / 4.0      # 取 0.25 的整数倍, 避免 1.249999 这类毛刺
    return min(max(s, 1.0), 3.0)


def font(size, bold=False, scale=1.0, mono=False):
    """像素字号字体(负数=像素高, 不随 tk scaling 漂移)；统一乘 FONT_BOOST"""
    return tkfont.Font(family=FONT_MONO if mono else FONT_FAMILY,
                       size=-max(9, int(round(size * FONT_BOOST * scale))),
                       weight="bold" if bold else "normal")


def vfade_photo(w, h, rgb, rgb_far=None, ss=4, blur=0.7):
    """竖线往下**收束到消失**：把右侧横向凸线那套（粗细 2→1、亮度 1→0、颜色 neon→浅灰）
    **竖过来**用 —— 用户要的就是"延申出去的那段跟右侧横向一样"。

    每行的表现 = 该行的"粗细覆盖率" × 亮度：
      · 亮度 alpha = 1 - t（线性，和凸线一样）；
      · 粗细：总宽从 2px 收到 1px，**从最外面那一列开始收**（最里侧最长、最外的最短）。
    给最左页签的竖框线用：它左边没地方铺横向凸线，就让它在框线**之外**往下自己收完。
    """
    w, h = max(1, int(w)), max(1, int(h))
    far = rgb_far or rgb
    im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    steps = h * ss
    for k in range(steps):
        t = (k + 0.5) / steps
        if t >= 1.0:
            break
        col = tuple(int(round(rgb[i] + (far[i] - rgb[i]) * t)) for i in range(3))
        for c in range(w):
            # c=0 是最外面那列: 覆盖率 (1-t)^(w-c) → 最外最快归零, 最里(倒数第一列)只跟亮度走
            a = (1.0 - t) ** (w - c)
            av = int(round(255 * max(0.0, a)))
            if av <= 0:
                continue
            d.rectangle([c * ss, k, c * ss + ss - 1, k], fill=(col[0], col[1], col[2], av))
    im = im.filter(ImageFilter.GaussianBlur(blur * ss))
    return ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))


def crown_photo(n, band_h, base_row, thick_at, alpha_at, rgb, ss=4):
    """把"凸线"画成一张**带透明通道的图片**（覆盖层），返回 PhotoImage。

    这是它该有的做法（前两版都不对，用户都指出来了）：
      · **纯霓虹色 + 透明度渐变**，叠在下面那条 1px 浅灰细线上 —— 看着就是"青 → 浅灰"，
        但它不会经过"青混灰"的浑浊中间色（直接插值两个颜色就会发浑、还容易出色阶断层）；
      · 粗细/透明度都按**浮点**给，在 ss 倍画布上落成亚像素矩形 —— 只要把粗细量化成整数
        （2px/3px），超采样也只能把台阶边磨圆，那三层台阶还在（用户："一层一层的"）；
      · 最后 LANCZOS 缩回目标尺寸、再轻微高斯模糊一次：边缘和色阶都被抹开，
        看上去是"柔和过渡"而不是"裁出来的一条"。
      · Tk 画布自己的线**完全没有抗锯齿**（这就是"锯齿感"的来源），所以这一条只能走图片。

    n/band_h: 图片宽高（物理像素）。**宽度要和画布的实际像素宽一致**，差 1px 就会在画布
    接缝处留一格没画到的分割线（用户实测："下面那个像素似乎缺了一小块"）。
    base_row: 底线在图片里的行号 —— 所有列**下缘对齐**它，粗的那头往上凸（"凸线"）。
    thick_at(i) → 第 i 列粗细（浮点，可以 1.5）；alpha_at(i) → 第 i 列不透明度 0~1。
    """
    n, h = max(1, int(n)), max(1, int(band_h))
    rgb3 = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    im = Image.new("RGBA", (n * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y1 = (base_row + 1) * ss                            # 下缘
    for i in range(n):
        w = max(0.0, float(thick_at(i)))
        a = max(0.0, min(1.0, float(alpha_at(i))))
        if w <= 0 or a <= 0:
            continue
        # 逐**行**按"覆盖率"落色, 而不是画一个整数高度的矩形: 粗细是从 2px 慢慢收到 1px 的,
        # 若把粗细四舍五入成整像素, 上面那一行只会有 4 档(≈"三段")、看着是跳变 —— 用户实测
        # "上面那条渐变的看起来只有三段, 颜色1 直接到颜色2 直接到3"。现在第 k 行的不透明度
        # = a × clamp(粗细-k, 0, 1), 于是那一行是**连续**淡出的。
        k = 0
        left = w
        while left > 1e-3:
            a_row = a * min(1.0, left)
            d.rectangle([i * ss, y1 - (k + 1) * ss, i * ss + ss - 1, y1 - k * ss - 1],
                        fill=rgb3 + (int(round(255 * a_row)),))
            left -= 1.0
            k += 1
    im = im.filter(ImageFilter.GaussianBlur(0.55 * ss))   # 横线本身只要一点点柔化
    return ImageTk.PhotoImage(im.resize((n, h), Image.LANCZOS))


class WindowDragger:
    """无边框窗口的拖动：绑在标题栏那块控件/画布上。

    Tk 的 overrideredirect 窗口没有原生标题栏，必须自己实现拖动。"""

    def __init__(self, win, widget, skip_tags=("winbtn",), on_release=None):
        self.win, self.widget, self.skip_tags = win, widget, skip_tags
        self.on_release = on_release
        self._start = None
        widget.bind("<ButtonPress-1>", self._press, add="+")
        widget.bind("<B1-Motion>", self._move, add="+")
        widget.bind("<ButtonRelease-1>", self._release, add="+")

    def _press(self, e):
        try:
            if self.skip_tags and hasattr(self.widget, "find_withtag"):
                cur = self.widget.find_withtag("current")
                if cur and any(t in self.widget.gettags(cur[0]) for t in self.skip_tags):
                    return
        except Exception:
            pass
        self._start = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _move(self, e):
        if not self._start:
            return
        dx = e.x_root - self._start[0]
        dy = e.y_root - self._start[1]
        self.win.geometry(f"+{self._start[2] + dx}+{self._start[3] + dy}")

    def _release(self, _e):
        if self._start and self.on_release:
            self.on_release(self.win.winfo_x(), self.win.winfo_y())
        self._start = None


class Skin:
    """皮肤素材加载与缩放缓存。缺图 -> img() 返回 None, 控件退回纯色样式。"""

    def __init__(self, base_dir, scale=1.0):
        self.dir = os.path.join(base_dir, "ui_skin")
        self.scale = scale
        self._cache = {}

    def img(self, name, size=None, dim=0.0):
        """name 不带 .png；size=(w,h) 逻辑尺寸(会乘缩放)；dim>0 时压暗(禁用态)"""
        key = (name, size, round(dim, 2))
        if key in self._cache:
            return self._cache[key]
        path = os.path.join(self.dir, name + ".png")
        ph = None
        if os.path.exists(path):
            try:
                im = Image.open(path).convert("RGBA")
                if dim > 0:
                    r, g, b, a = im.split()
                    lut = [int(v * (1 - dim)) for v in range(256)]
                    im = Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))
                if size:
                    im = im.resize((max(1, int(size[0] * self.scale)),
                                    max(1, int(size[1] * self.scale))), Image.LANCZOS)
                elif self.scale != 1.0:
                    im = im.resize((max(1, int(im.width * self.scale)),
                                    max(1, int(im.height * self.scale))), Image.LANCZOS)
                ph = ImageTk.PhotoImage(im)
            except Exception:
                ph = None
        self._cache[key] = ph
        return ph


class ToolTip:
    """悬停提示(术语解释用它, 文案一字不改地用 docs/UI说明.md §6 的原文)"""

    def __init__(self, widget, text_getter, skin=None, scale=1.0, delay=350):
        self.widget, self.get_text = widget, text_getter
        self.scale, self.delay = scale, delay
        self.tip = None
        self._after = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        text = self.get_text() if callable(self.get_text) else self.get_text
        if not text or self.tip:
            return
        try:
            x = self.widget.winfo_rootx() + int(16 * self.scale)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + int(4 * self.scale)
        except Exception:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=text, justify="left", bg=C["metal_dark"], fg=C["text"],
                 font=font(9, scale=self.scale), wraplength=int(360 * self.scale),
                 bd=0, padx=int(10 * self.scale), pady=int(7 * self.scale)).pack()
        self.tip.geometry(f"+{x}+{y}")

    def _leave(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class ImgButton(tk.Canvas):
    """图片按钮: 三态 PNG + 两行文字(名字 / 快捷键)。没有素材时退回纯色方块。

    kind: primary | btn | wide | small；small 只画一行文字。
    """

    def __init__(self, master, text, key="", kind="btn", command=None,
                 skin=None, scale=1.0, tooltip=None, font_size=9, bold_text=None, bg=None,
                 accent=None):
        self.w, self.h = BTN_SIZES[kind]
        w, h = int(self.w * scale), int(self.h * scale)
        self._bg = bg or C["bg"]
        super().__init__(master, width=w, height=h, bg=self._bg, highlightthickness=0, bd=0)
        self.kind, self.command, self.skin, self.scale = kind, command, skin, scale
        self.enabled = True
        self._state = "normal"
        self._text, self._key = text, key
        # 单行按钮的字号/加粗: font_size 由调用方给(底部那排常驻按钮要"更大更粗"做强调);
        # bold_text=None 时按 accent 自动决定 —— 强调按钮 = 加粗, 一眼能看出是重点。
        self._font_size = font_size
        self._bold_text = bold_text
        self._key_dim = False
        self._key_color = None      # 显式指定键位行颜色(None=按档位默认)
        self._lamp = None           # 右上角小灯(lamp_good/lamp_off...)：表示"开/关"状态用
        self._accent = accent       # 强调色(None=按档位; True=主色, 用于"常驻提示类"按钮)
        self._focus = False         # 键盘焦点(Tab 走查): 见 _draw 里的焦边框
        self._bg_img = None
        self._draw()
        # 手型光标: 以前只在 set_enabled() 里设, 于是"从头到尾都是启用态、没经历过
        # 禁用→启用"的按钮(正常使用时绝大多数按钮)全程显示箭头, 悬停手感不一致
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        # 键盘可达性: 允许 Tab 遍历 + 回车/空格触发。无边框自绘窗口里 Tk 的默认焦点框
        # 看不见(被皮肤盖住), 所以 _draw 里自己画一圈 —— 否则键盘用户不知道焦点在哪,
        # 按空格更不知道会触发什么(用户建议加的)。
        self.configure(takefocus=1)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_key_activate)
        self.bind("<space>", self._on_key_activate)
        if tooltip:
            ToolTip(self, tooltip, scale=scale)

    # ---- 绘制 ----
    def _draw(self):
        self.delete("all")
        w, h = int(self.w * self.scale), int(self.h * self.scale)
        name = {"primary": "btn_primary", "hero": "btn_hero", "big": "btn_big",
                "btn": "btn", "wide": "btn_wide", "small": "btn_small",
                "mini": "btn_mini"}[self.kind]
        if not self.enabled:
            st = "normal"
        else:
            st = self._state
        ph = self.skin.img(f"{name}_{st}", (self.w, self.h), dim=0.55 if not self.enabled else 0.0) \
            if self.skin else None
        if ph:
            self._bg_img = ph
            self.create_image(0, 0, anchor="nw", image=ph)
        else:
            self.create_rectangle(0, 0, w - 1, h - 1, outline=C["metal_light"],
                                  fill=C["panel"] if self.enabled else C["bg"])
        fg = C["text"] if self.enabled else C["off"]
        accent = (self.kind in ("primary", "hero")) if self._accent is None else bool(self._accent)
        if not self.enabled:
            fg2 = C["off"]
        elif getattr(self, "_key_dim", False):
            fg2 = C["off"]
        elif getattr(self, "_key_color", None):
            fg2 = self._key_color          # 由调用方指定的状态色(总开关"启用中/已暂停")
        else:
            fg2 = C["neon"] if accent else C["text_dim"]
        if getattr(self, "_focus", False):
            # 焦边框: 贴边一圈亮色(2px), 与悬停/禁用态都不冲突
            self.create_rectangle(1, 1, w - 2, h - 2, outline=C["neon"], width=2)
        big = font(12 if self.kind == "primary" else 10, bold=accent, scale=self.scale)
        small = font(9, scale=self.scale)
        # 视觉居中：两行以按钮中线为轴对称（原来 0.38/0.72 看着偏下），
        # 单行再往上抬一点点——CJK 字形的视觉重心比字框中心略低（用户实测反馈）
        if self.kind in ("small", "mini") or not self._key:
            # 单行按钮: 强调色直接作用在这行字上(底部那排"使用说明/免责声明/杀软白名单"用)。
            # 字号/加粗由调用方给 —— font_size 一直有参数却没人用(永远是 9 号细体), 现在接上。
            bold1 = accent if self._bold_text is None else bool(self._bold_text)
            fill1 = C["neon"] if (accent and self.enabled) else fg
            self.create_text(w / 2, h * 0.46, text=self._text, fill=fill1,
                             font=font(self._font_size, bold=bold1, scale=self.scale))
        else:
            self.create_text(w / 2, h * 0.30, text=self._text, fill=fg, font=big)
            self.create_text(w / 2, h * 0.70, text=self._key, fill=fg2, font=small)
        self._draw_lamp(w)

    def _draw_lamp(self, w):
        """右上角小灯: 让"开/关"这种状态在按钮**本身上**看得出来(不只是文字)。

        素材缺了也不报错——退回纯色小圆点。灯的位置固定在右上角内缘,
        按钮文字是居中的, 240 宽的档位下不会撞上。
        """
        if not self._lamp:
            return
        s, size = self.scale, 11
        x, y = w - int((size + 8) * s), int(8 * s)
        ph = self.skin.img("lamp_" + self._lamp, (size, size)) if self.skin else None
        if ph:
            self._lamp_img = ph
            self.create_image(x, y, anchor="nw", image=ph)
        else:
            r = int(size * s / 2)
            self.create_oval(x + int(5 * s) - r, y + int(5 * s) - r,
                             x + int(5 * s) + r, y + int(5 * s) + r,
                             fill=C.get(self._lamp, C["off"]), outline="")

    # ---- 状态 ----
    def set_text(self, text, key=None, key_dim=False, key_color=None, lamp=None):
        """key_dim=True: 下面那行键位用更暗的灰显示(总开关暂停时显示"已关闭"用)
        key_color: 显式指定键位行颜色; lamp: 右上角小灯(lamp_good/lamp_off/...), None=不画"""
        self._text = text
        if key is not None:
            self._key = key
        self._key_dim = bool(key_dim)
        self._key_color = key_color
        self._lamp = lamp
        self._draw()

    def set_enabled(self, on):
        if on != self.enabled:
            self.enabled = bool(on)
            self._state = "normal"
            self._draw()
            self.configure(cursor="hand2" if on else "arrow")

    def _on_focus_in(self, _e):
        self._focus = True
        self._draw()

    def _on_focus_out(self, _e):
        self._focus = False
        self._draw()

    def _on_key_activate(self, _e):
        """回车/空格 = 点一下(键盘可达)"""
        if self.enabled and self.command:
            self.command()
            return "break"

    def _on_enter(self, _e):
        if self.enabled:
            self._state = "hover"
            self._draw()

    def _on_leave(self, _e):
        if self.enabled:
            self._state = "normal"
            self._draw()

    def _on_press(self, _e):
        if self.enabled:
            self._state = "pressed"
            self._draw()

    def _on_release(self, e):
        if not self.enabled:
            return
        self._state = "hover"
        self._draw()
        # 只有松开时鼠标还在按钮上才算点击
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height() and self.command:
            self.command()


class Lamp(tk.Frame):
    """状态灯: 发光圆点 + 文字（文字只用面向用户的说法, 不出现 NOP/R 这类内部术语）"""

    def __init__(self, master, text, skin, scale=1.0, kind="off", tooltip=None, bg=None):
        self._bg = bg or C["bg"]
        super().__init__(master, bg=self._bg)
        size = 14
        self.canvas = tk.Canvas(self, width=int(size * scale), height=int(size * scale),
                                bg=self._bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left")
        self.label = tk.Label(self, text=text, bg=self._bg, fg=C["text"],
                              font=font(9, scale=scale))
        self.label.pack(side="left", padx=(int(8 * scale), 0))
        self.skin, self.scale, self._kind = skin, scale, kind
        self.set(kind, text)
        if tooltip:
            ToolTip(self.label, tooltip, scale=scale)

    def set(self, kind, text=None):
        self._kind = kind
        if text is not None:
            self.label.configure(text=text)
        self.canvas.delete("all")
        ph = self.skin.img(f"lamp_{kind}", (14, 14)) if self.skin else None
        if ph:
            self._ph = ph
            self.canvas.create_image(0, 0, anchor="nw", image=ph)
        else:
            col = C.get(kind, C["off"])
            r = 14 * self.scale / 2
            self.canvas.create_oval(0, 0, r * 2, r * 2, fill=col, outline="")


def style_ttk(root, scale=1.0):
    """把 ttk 的 Checkbutton/Entry/Combobox/Scrollbar 调成深色(clam 主题才吃颜色)"""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure("TCheckbutton", background=C["bg"], foreground=C["text"],
                 font=font(9, scale=scale), focuscolor=C["bg"])
    st.map("TCheckbutton", background=[("active", C["bg"])],
           foreground=[("disabled", C["off"])])
    st.configure("TEntry", fieldbackground=C["metal_dark"], foreground=C["text"],
                 insertcolor=C["text"], bordercolor=C["metal_light"],
                 lightcolor=C["metal_light"], darkcolor=C["metal_light"], padding=3)
    st.configure("TCombobox", fieldbackground=C["metal_dark"], background=C["metal"],
                 foreground=C["text"], arrowcolor=C["neon"], bordercolor=C["metal_light"])
    st.map("TCombobox", fieldbackground=[("readonly", C["metal_dark"])],
           foreground=[("readonly", C["text"])])
    # 滚动条: 经典 tk.Scrollbar 在 Windows 上由系统主题绘制、颜色参数基本无效(白得扎眼),
    # 所以界面里一律用 ttk.Scrollbar + 这里这套深色配色
    st.configure("TScrollbar", background=C["metal_light"], troughcolor=C["metal_dark"],
                 bordercolor=C["metal_dark"], darkcolor=C["metal_light"],
                 lightcolor=C["metal_light"], arrowcolor=C["text_dim"], relief="flat",
                 gripcount=0)
    st.map("TScrollbar", background=[("active", C["neon"]), ("!active", C["metal_light"])],
           arrowcolor=[("active", C["neon"])])
    st.configure("TFrame", background=C["bg"])
    st.configure("TLabel", background=C["bg"], foreground=C["text"], font=font(9, scale=scale))
    return st
