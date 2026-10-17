# -*- coding: utf-8 -*-
"""Кольцевая диаграмма «доля отвеченных» в PNG без внешних библиотек.
Зелёный сектор — отвечено, красный — остаток. Растр 4x с усреднением (сглаживание),
объёмный вид: блик ближе к внешнему краю кольца и мягкая тень снизу."""
import math, struct, zlib

GREEN = (39, 174, 96)
RED = (229, 57, 53)
GREY = (200, 204, 210)
SHADOW = (20, 30, 50)

def render(rate, size=96, thickness=0.34, path=None):
    """rate 0..1 (None — нет данных). Возвращает bytes PNG, при path пишет файл."""
    ss = 4
    S = size * ss
    cx = cy = S / 2.0
    R = S / 2.0 - 4 * ss          # место под тень
    r_in = R * (1 - thickness)
    ang_green = (rate or 0.0) * 2 * math.pi
    sh_dx, sh_dy = 0, 2.5 * ss    # смещение тени
    transparent = bytes((0, 0, 0, 0))
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    px = x * ss + sx + 0.5 - cx
                    py = y * ss + sy + 0.5 - cy
                    d = math.hypot(px, py)
                    if r_in <= d <= R:
                        a = math.atan2(px, -py)          # 0 сверху, по часовой
                        if a < 0:
                            a += 2 * math.pi
                        base = GREY if rate is None else (GREEN if a <= ang_green else RED)
                        t = (d - r_in) / (R - r_in)               # 0 внутренний край, 1 внешний
                        f = 0.72 + 0.42 * (1 - abs(2 * t - 0.9))  # блик ближе к внешнему краю
                        acc[0] += min(255, base[0] * f); acc[1] += min(255, base[1] * f); acc[2] += min(255, base[2] * f)
                        acc[3] += 255
                    else:
                        ds = math.hypot(px - sh_dx, py - sh_dy)
                        pass  # тень отключена по просьбе Олега (выглядела как серая зона)
            n = ss * ss
            if acc[3]:
                cov = int(min(255, acc[3] / n))
                if acc[0] or acc[1] or acc[2]:
                    k = acc[3] / 255.0
                    row += bytes((int(acc[0] / k), int(acc[1] / k), int(acc[2] / k), cov))
                else:
                    row += bytes((SHADOW[0], SHADOW[1], SHADOW[2], cov))
            else:
                row += transparent
        rows.append(bytes((0,)) + bytes(row))   # фильтр строки PNG = 0
    raw = b"".join(rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    if path:
        with open(path, "wb") as f:
            f.write(png)
    return png
