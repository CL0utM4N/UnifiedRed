#!/usr/bin/env python3
"""
Dump UVD's registers on Linux while amdgpu + Mesa decode a video, for comparison with UnifiedRed's set-up
(docs/VIDEO.md). Run from an Ubuntu live session on the same machine:

    sudo python3 linux_uvd_dump.py <video file>

It decodes the video in a loop with VA-API (ffmpeg -hwaccel vaapi, output discarded), reads the UVD register block
(dwords 0x3B00-0x3DFF) plus a few related registers from /sys/kernel/debug/dri/N/amdgpu_regs five times, one second
apart, and writes everything to uvd-linux-dump.txt next to this script, with vainfo, kernel, lspci and amdgpu
dmesg lines. Only reads registers; amdgpu itself drives the hardware.
"""
import glob
import os
import struct
import subprocess
import sys
import time

UVD_FIRST, UVD_LAST = 0x3B00, 0x3DFF
EXTRA = {0x263E: "GB_ADDR_CONFIG", 0x0398: "SRBM_SOFT_RESET", 0x0394: "SRBM_STATUS", 0x0393: "SRBM_STATUS2"}
SAMPLES = 5


def run(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # keep going: a missing tool shouldn't lose the dump
        return f"({cmd}: {e})\n"


def find_regs():
    for d in sorted(glob.glob("/sys/kernel/debug/dri/*")):
        name = run(f"cat {d}/name")
        if "amdgpu" in name and os.path.exists(f"{d}/amdgpu_regs"):
            return f"{d}/amdgpu_regs", name.strip()
    return None, None


def read_block(path, first, last):
    out = {}
    with open(path, "rb", buffering=0) as f:
        for reg in range(first, last + 1):
            f.seek(reg * 4)
            data = f.read(4)
            if len(data) == 4:
                out[reg] = struct.unpack("<I", data)[0]
    return out


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):
        sys.exit("usage: sudo python3 linux_uvd_dump.py <video file>")
    video = sys.argv[1]
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uvd-linux-dump.txt")
    run("mount -t debugfs none /sys/kernel/debug 2>/dev/null")
    regs, name = find_regs()
    if not regs:
        sys.exit("amdgpu_regs not found under /sys/kernel/debug/dri (is amdgpu loaded?)")

    lines = [f"linux uvd dump; {name}", "uname: " + run("uname -a").strip(), "lspci: " + run("lspci -nn | grep -i vga").strip(),
             "vainfo:", run("vainfo 2>&1"), "amdgpu dmesg:", run("dmesg | grep -i -E 'amdgpu|uvd|vce' | tail -60")]

    dec = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-hwaccel", "vaapi", "-hwaccel_device",
                            "/dev/dri/renderD128", "-stream_loop", "-1", "-i", video, "-f", "null", "-"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    if dec.poll() is not None:
        lines.append("ffmpeg stopped early: " + dec.stderr.read())
    samples = []
    for i in range(SAMPLES):
        block = read_block(regs, UVD_FIRST, UVD_LAST)
        for reg in EXTRA:
            block.update(read_block(regs, reg, reg))
        samples.append(block)
        time.sleep(1)
    dec.terminate()
    try:
        err = dec.communicate(timeout=10)[1]
    except subprocess.TimeoutExpired:
        dec.kill()
        err = ""
    lines.append("ffmpeg errors: " + (err.strip() or "none"))

    lines.append(f"registers (dword index: value per sample, {SAMPLES} samples 1 s apart during decode):")
    for reg in sorted(samples[0]):
        vals = " ".join(f"{s.get(reg, 0):08x}" for s in samples)
        label = EXTRA.get(reg, "")
        lines.append(f"{reg:04x}: {vals} {label}".rstrip())
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", out_path)


if __name__ == "__main__":
    main()
