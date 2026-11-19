#!/bin/sh
# Cross-build "Zbir Cliping.exe" from Linux/macOS (pip install ziglang)
cd "$(dirname "$0")"
python3 -m ziglang cc -target x86_64-windows-gnu -O2 -s -municode -Wl,--subsystem,windows \
  launcher.c launcher.rc -o "../Zbir Cliping.exe" -lshell32 -luser32 && rm -f ../*.pdb && echo OK
