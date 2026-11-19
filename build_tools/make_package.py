# -*- coding: utf-8 -*-
"""Собирает папки установки и обновления из репозитория и готового ReceptionMail.exe.
Запуск: python build_tools/make_package.py <путь к ReceptionMail.exe> <папка для результата>
  <результат>/install — полная установка (распаковать в пустую C:\\ReceptionMail);
  <результат>/update  — обновление: без config.json, data и reports (настройки и база писем не трогаются)."""
import os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def version():
    with open(os.path.join(ROOT, "app", "core.py"), encoding="utf-8") as f:
        return f.read().split('VERSION = "', 1)[1].split('"', 1)[0]

def build(exe, out):
    for kind in ("install", "update"):
        dst = os.path.join(out, kind)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        if exe:
            shutil.copy2(exe, os.path.join(dst, "ReceptionMail.exe"))
        for d in ("app", "ui", "sounds"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(os.path.join(ROOT, "INSTRUKCIYA.md"), dst)
        for f in sorted(os.listdir(os.path.join(ROOT, "docs"))):
            if f.lower().endswith(".pdf"):
                shutil.copy2(os.path.join(ROOT, "docs", f), dst)
        if kind == "install":
            shutil.copy2(os.path.join(ROOT, "config.json"), dst)
            for d in ("data", "reports"):
                os.makedirs(os.path.join(dst, d))
                open(os.path.join(dst, d, ".keep"), "w").close()
    return version()

if __name__ == "__main__":
    exe = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ("", "-") else ""
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "dist_pkg")
    if exe and not os.path.isfile(exe):
        sys.exit("нет файла " + exe)
    print(build(exe, out))
