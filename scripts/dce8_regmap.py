#!/usr/bin/env python3
"""
Generate UnifiedRed/kern_dce8map.hpp: the register translation that lets Apple's Polaris display controller
(AMD9500Controller) and accelerator (AMDRadeonX4000's VI layer), DCE 11.2 / GMC 8.1 / BIF 5.0 / OSS 3.0 / GFX 8,
drive a GCN 2 APU (DCE 8 / GMC 7.1 / BIF 4.1 / OSS 2.0 / GFX 7).

    python scripts/dce8_regmap.py <dir with the Linux headers> [--report report.txt]

<dir> holds Linux amdgpu's asic_reg headers (MIT licensed): dce_11_2_d.h, dce_8_0_d.h, gmc_8_1_d.h, gmc_7_1_d.h,
bif_5_0_d.h, bif_4_1_d.h, oss_3_0_d.h, oss_2_0_d.h, gfx_8_0_d.h, gfx_7_0_d.h and the matching *_sh_mask.h.

Every direct (mm) register of the newer header is matched by name, after a few renames (HPD, master update), to the
older one. The table lists each newer offset whose register moved (to = older offset) or has no older counterpart
(to = 0xFFFF: the write is dropped, a read returns 0). An offset that isn't listed passes through unchanged: it is
either the same register on both, or no register of the newer blocks at all (GFX, SMU, ...).
The report lists the mapped pairs whose bit fields differ, which an offset change alone doesn't fix.
"""

import re
import sys
from pathlib import Path

DEFINE = re.compile(r"^#define\s+mm(\w+)\s+(0x[0-9A-Fa-f]+)")
FIELD = re.compile(r"^#define\s+(\w+?)__(\w+)_MASK\s+(0x[0-9A-Fa-f]+)")
BLOCKS = [("dce_11_2", "dce_8_0"), ("gmc_8_1", "gmc_7_1"), ("bif_5_0", "bif_4_1"), ("oss_3_0", "oss_2_0"),
          ("gfx_8_0", "gfx_7_0")]
# The register an aliased GFX 8 offset means to the accelerator: RLC safe mode (its VI layer enters it), the CP ME RAM
# write port (microcode is written, CAIL's job, never read back).
PREFER = {"RLC_SAFE_MODE", "CP_ME_RAM_WADDR"}


def regs(path):
    out = {}
    for line in path.read_text().splitlines():
        m = DEFINE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def fields(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = FIELD.match(line)
        if m:
            out.setdefault(m.group(1), {})[m.group(2)] = int(m.group(3), 16)
    return out


def candidates(name):
    """Older-generation names a newer register may have, most specific first."""
    yield name
    m = re.match(r"HPD(\d)_DC_HPD_(\w+)$", name)
    if m:
        yield "DC_HPD%d_%s" % (int(m.group(1)) + 1, m.group(2))
    m = re.match(r"DC_HPD_(\w+)$", name)
    if m:
        yield "DC_HPD1_" + m.group(1)
    m = re.match(r"(CRTC\d_)?CRTC_MASTER_UPDATE_(\w+)$", name)
    if m:
        yield (m.group(1) or "") + "MASTER_UPDATE_" + m.group(2)


def main():
    base = Path(sys.argv[1])
    report_path = sys.argv[sys.argv.index("--report") + 1] if "--report" in sys.argv else None
    table, report, stats = {}, [], []
    for new, old in BLOCKS:
        a, b = regs(base / (new + "_d.h")), regs(base / (old + "_d.h"))
        fa, fb = fields(base / (new + "_sh_mask.h")), fields(base / (old + "_sh_mask.h"))
        by_offset = {}
        for name, off in a.items():
            by_offset.setdefault(off, []).append(name)
        moved = dropped = same = conflicts = 0
        for off, names in sorted(by_offset.items()):
            targets = {}
            for name in names:
                for cand in candidates(name):
                    if cand in b:
                        targets.setdefault(b[cand], []).append((name, cand))
                        break
            if not targets:
                to = 0xFFFF
                dropped += 1
            else:
                # Several names for one offset should agree; if not, a preferred name (aliases on GFX 8: RLC_SAFE_MODE
                # = RLC_SOFT_RESET_GPU, CP_ME_RAM_RADDR = WADDR) decides, else the one most names agree on.
                preferred = [t for t, pairs in targets.items() if any(n in PREFER for n, _ in pairs)]
                to = preferred[0] if preferred else max(targets, key=lambda t: len(targets[t]))
                if len(targets) > 1:
                    conflicts += 1
                    report.append("conflict %s 0x%x: %s" % (new, off, targets))
                if to == off:
                    same += 1
                else:
                    moved += 1
                name, cand = targets[to][0]
                if name in fa and cand in fb and fa[name] != fb[cand]:
                    diff = sorted(set(fa[name].items()) ^ set(fb[cand].items()))
                    report.append("fields %s 0x%x->0x%x %s/%s: %s" % (new, off, to, name, cand,
                        ", ".join("%s=%x" % d for d in diff)))
            if to != off:
                if off in table and table[off] != to:
                    report.append("overlap 0x%x: %s says 0x%x, %s says 0x%x" % (off, new, to, "earlier", table[off]))
                    continue
                table[off] = to
        stats.append("%s -> %s: %d offsets, %d same, %d moved, %d dropped, %d conflicts" %
            (new, old, len(by_offset), same, moved, dropped, conflicts))
    for off in table:
        assert off < 0x10000 and (table[off] < 0x10000), hex(off)

    out = Path(__file__).resolve().parent.parent / "UnifiedRed" / "kern_dce8map.hpp"
    lines = [
        "//",
        "//  kern_dce8map.hpp",
        "//  UnifiedRed",
        "//",
        "//  Generated by scripts/dce8_regmap.py from Linux amdgpu's asic_reg headers (MIT licensed). Do not edit.",
        "//  Polaris (DCE 11.2, GMC 8.1, BIF 5.0, OSS 3.0, GFX 8) register dword offsets that differ on GCN 2 APUs (DCE 8,",
        "//  GMC 7.1, BIF 4.1, OSS 2.0, GFX 7): the GCN 2 offset, or Dce8Map::None for a register GCN 2 doesn't have.",
        "//  Offsets not listed are the same register on both, or not in these blocks.",
        "//  " + "; ".join(stats),
        "//",
        "",
        "#ifndef kern_dce8map_hpp",
        "#define kern_dce8map_hpp",
        "",
        "#include <stdint.h>",
        "",
        "namespace Dce8Map {",
        "\tstatic constexpr uint16_t None = 0xFFFF;",
        "\tstruct Entry {",
        "\t\tuint16_t from, to;",
        "\t};",
        "\tstatic constexpr Entry Entries[] {",
    ]
    row = []
    for off in sorted(table):
        row.append("{0x%04X, 0x%04X}," % (off, table[off]))
        if len(row) == 6:
            lines.append("\t\t" + " ".join(row))
            row = []
    if row:
        lines.append("\t\t" + " ".join(row))
    lines += [
        "\t};",
        "",
        "\t// Binary search; returns `from` itself for an offset that isn't listed.",
        "\tinline uint32_t translate(uint32_t from) {",
        "\t\tsize_t lo = 0, hi = sizeof(Entries) / sizeof(Entries[0]);",
        "\t\twhile (lo < hi) {",
        "\t\t\tsize_t mid = (lo + hi) / 2;",
        "\t\t\tif (Entries[mid].from < from)",
        "\t\t\t\tlo = mid + 1;",
        "\t\t\telse",
        "\t\t\t\thi = mid;",
        "\t\t}",
        "\t\treturn lo < sizeof(Entries) / sizeof(Entries[0]) && Entries[lo].from == from ? Entries[lo].to : from;",
        "\t}",
        "}",
        "",
        "#endif /* kern_dce8map_hpp */",
        "",
    ]
    out.write_text("\n".join(lines), newline="\n")
    print("\n".join(stats))
    print("%d table entries -> %s" % (len(table), out))
    if report_path:
        Path(report_path).write_text("\n".join(stats + report) + "\n")
        print("%d report lines -> %s" % (len(report), report_path))


if __name__ == "__main__":
    main()
