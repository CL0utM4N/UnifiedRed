# Test machines (GCN 2)

## HP laptop, AMD A4-5000 (Kabini)

| | |
|---|---|
| CPU | A4-5000: 4 × Jaguar, 1.5 GHz (family 16h model 0 stepping 1). AVX, BMI1, MOVBE, F16C; **no AVX2/BMI2/FMA**, so macOS uses the plain `x86_64` dyld shared cache, not `x86_64h` |
| GPU | Radeon HD 8330: **1002:9832 rev 0x00**, subsystem 103C:8133; GCN 2 gfx703, 2 CUs, DCE 8.3 |
| Model, firmware | HP Notebook, board 8133, Insyde BIOS **F.12 (2015-07-30)**, KBC 98.0C |
| Memory | 2 × 4 GB DDR3-1333 (dual channel, 128-bit) |
| UMA carve-out | **512 MB**; BAR0 256 MB at `0xE0000000` (smaller than the carve-out), MMIO (BAR5) 256 KB at `0xF2900000`, doorbells 8 MB at `0xF2000000`. E820 reserves `0xBFC00000`-`0xDFFFFFFF`, which presumably holds the carve-out |
| Display | eDP panel 1366×768 (HPD1), HDMI (HPD2), VGA (DAC1) |
| VBIOS | "AMD Fusion Kalindi Generic VBIOS", 2015-03-18, 59,904 bytes; SetPixelClock rev 6 (Apple's `AtiSetPixelClock_V1_V6`), PowerPlayInfo rev 6.1, IntegratedSystemInfo rev 1.8 |
| **No ACPI VFCT table** | the VBIOS isn't published through ACPI. KaberiRed falls back to the legacy ROM shadow at `0xC0000`; the dumped copy (`KaberiRed-RE/…/gpu/pci-rom.bin`) can be injected as `ATY,bin_image` through DeviceProperties if that fails |
| Other devices | FCH xHCI 1022:7814, EHCI/OHCI 1022:7808/7807, SATA AHCI 1022:7804, SD 1022:7813, HD Audio 1022:780D, HDMI audio 1002:9840, Realtek RTL810xE Ethernet 10EC:8136, Intel Wireless 7260 8086:08B1 |

Under Linux (`radeon`, kernel 6.17) the GPU comes up completely: kabini microcode, all 8 rings and IB tests pass, UVD
1.64 and VCE 40.2.2, DPM on, MSI interrupts.

Jaguar is a family 16h CPU: macOS needs the AMD_Vanilla kernel patches for 15h/16h, as on CarrizoRed's machines.
On CarrizoRed's APUs the FCH's USB 3 controller never delivered interrupts to macOS (see `FCHXHCIFix`); Kabini's
on-die FCH is a different generation, so check USB on the first boot before blaming anything else.

## Phase 0: reference data from Linux

Boot a Linux live USB (Ubuntu or Fedora) with Secure Boot off, ideally with the kernel parameters
`radeon.cik_support=0 amdgpu.cik_support=1` (so `amdgpu` drives the GPU and exposes its registers), then:

```sh
sudo bash scripts/collect_linux_info.sh
```

It's read-only and writes `kaberired-dump-<host>-<date>.tar.gz` next to itself: PCI config space, ACPI tables
(including VFCT, the VBIOS), the GPU driver's log, CPU flags, and the GPU registers that matter here (carve-out
location, `GB_ADDR_CONFIG`, tiling, raster config, both display pipes, panel power sequencer). Copy it to the Windows
PC into `Desktop\KaberiRed-RE\`.

## First macOS boots

The EFI comes from `scripts/build_efi_hp.py` (OpenCore 1.0.8, AMD_Vanilla with 4 cores, iMac19,1, SSDT-EC-USBX,
VoodooPS2 with the Elantech trackpad, AirportItlwm for the Intel 7260, Monterey Recovery from `macrecovery.py`).

**USB stick:** format it FAT32 (GPT or MBR) and copy the contents of `KaberiRed-RE/efi-hp/usb/` (the `EFI` and
`com.apple.recovery.boot` folders) to its root. **BIOS (F10):** Secure Boot off, legacy/CSM boot off if offered,
USB boot on. Boot it with F9 (boot menu), **from a USB 2.0 port**: those are on the EHCI controllers, which macOS
supports natively; the FCH xHCI (1022:7814) is untested under macOS. In OpenCore's picker choose the Recovery entry;
join Wi-Fi in Recovery (no Ethernet driver), erase the internal disk as APFS in Disk Utility, then Reinstall macOS
Monterey.

### Install log
- **2026-10-17, first EFI:** black screen, no picker; the OpenCore file log was 256 KB of zeros. With `--debugoc
  --diag` (log on screen through the firmware's text console) it stopped at `Driver HfsPlus.efi at 1 is being
  loaded`: Apple's `HfsPlus.efi` executes **RDRAND, which Jaguar lacks**. `HfsPlusLegacy.efi` (no RDRAND) fixed it:
  the picker appears and Recovery boots.
- **Recovery, 4 cores:** stops at `dlil_init: Waiting for all the create dlil kernel threads…`: threads queued on the
  other cores never run. Not yet understood (the MADT is normal: LAPIC IDs 0-3, two I/O APICs). `cpus=1` gets past it.
- **Recovery, 1 core (with and without AirportItlwm):** the screen stops right after `rooting via boot-uuid`. With
  XHCIProbe (observe-only) it printed its 2 s report once and never again, so the kernel itself stopped, not just the
  root mount: the stick was seen (xHCI port 4, high speed, link in U0), but the 1 ms sampler got only 149 of ~2000
  samples in its first 2 s, as if the CPU was nearly saturated by interrupts. FCHXHCIFix (matched to 7814) never
  started. Hypothesis: an interrupt storm from the FCH's EHCI/OHCI controllers (shared legacy IRQs 17/18; Linux applies
  AMD-specific EHCI quirks to them). Next test: `--nousb2` keeps Apple's EHCI/OHCI drivers off them.
- **`--nousb2`:** same freeze (138 samples in the first 2 s window), so not EHCI/OHCI. Caps Lock dead at the freeze:
  the CPU is stuck, not just the screen. The OpenCore log (DEBUG) is clean: FID 0x14 DID 1, TSC 1497 MHz, **FSB 99.8
  MHz** passed to the kernel (`OCAK: BusFreq = 99812867Hz`), MSR 35h 4/4, every AMD_Vanilla patch applied, every kext
  injected, AppleIntelCPUPowerManagement dummied, ExitBootServices reached. Next: a baseline with only Lilu and
  VirtualSMC (`--minimal`), to separate the add-on kexts (battery/EC, PS/2) from macOS itself.
- **Baseline (Lilu + VirtualSMC):** froze *earlier*, right after `CoreAnalyticsHub`, before USB or PS/2. Without
  ECEnabler the EC's `_REG` fails (`AE_BAD_PARAMETER` from the EmbeddedControl handler in `EC0.ITLB`/`ECRI`/`_REG`):
  ECEnabler is required on this DSDT. The freeze point moves with what's loaded, so it isn't one driver: something
  arrives over time and swamps the CPU. **The battery is dead**, and every earlier boot logged SMCBatteryManager's
  "battery 0 has no remaining capacity". Lead: an EC event (SCI/GPE 3) flood, e.g. `_Q09` (battery status). Next
  test: `--minimal --noecgpe` (Lilu, VirtualSMC, ECEnabler; EC0's `_GPE` renamed so macOS ignores EC events).
- **`--noecgpe`:** froze even earlier (after `AMFILoadTrustedKeysFromNVRam`, ~3.6 s): not EC events. All freezes so
  far fall ~3-4 s into the kernel, at whatever line is current.
- **Platform probe (KaberiRed `-krplatprobe`, every 250 ms):** three lines before the freeze, all healthy: TSC and ACPI
  PM timer agree (267/260/255 ms per 250 ms window; HPET disabled), no vector pending or in service at the local
  APIC, APIC timer vector 0xDD one-shot 10 ms, PM1 `0010/0120`, GPE0 normal (EC event pending once, then clear), I/O
  APIC pins 9 (SCI) and 19 level-triggered without remote IRR. So no storm and no stuck event: the machine stops
  abruptly, interrupts dead, no panic, i.e. the CPU is in firmware code that doesn't return. NVRAM (`nvram0.bin`)
  holds no variable written by macOS. Next test: `--novarwrite` (OpenRuntime's `DisableVariableWrite`, refusing
  macOS's NVRAM writes, which go through the Insyde firmware's SMM variable service).
- **`--novarwrite`:** same freeze: not NVRAM. But the probe showed the mechanism: `KRP 6` at t=1817 ms, then no
  `KRP 7`, while other threads went on printing (`AppleUSBLegacyRoot` at 3.66 s, `MAC Framework`, `AMFI…`, `mcache…`)
  until the screen stopped. **The probe's 250 ms timer sleep never woke**: the timer interrupt (and the keyboard's)
  stops reaching the CPU, and the machine dies once every thread waits on a timer. XHCIProbe did the same (one report,
  then nothing). On AMD with no CPU PM kext, XNU idles with plain `sti; hlt` (`machine_idle`, pmCPU.c); all cores
  halted can mean C1E or an SMM trap (MSR C001_0055 CmpHalt bits). Linux idles this machine through `_CST` C2 = I/O
  read of port 0x414 (400 µs) and HLT only for short idles. The laptop has no fan (the firmware expects one).
  Next test: `--nohlt` (kernel patch `sti; hlt` → `sti; nop` in `_machine_idle`, 21.x): if the freezes stop, idle is
  the cause.

1. Monterey 12.7.6 with OpenCore and AMD_Vanilla, **no KaberiRed**: does it reach the desktop on the firmware
   framebuffer? Note the boot-args and SMBIOS used.
2. KaberiRed (debug) + Lilu (debug), **no spoof**, boot-args `-krdbg krpanicat=60 debug=0x100 keepsyms=1`: at 60 s
   the machine panics on purpose with KaberiRed's report (`debug=0x100` keeps it on screen). After rebooting, dump
   NVRAM from OpenShell (`dmpstore -all -s fs0:\nvramNNN.bin`) and send the file; `scripts/dmpstore.py` decodes it.
3. Only then the `device-id` spoof, with `-krnoaccel` so the display controller comes up on its own.

Keep a USB stick with the unspoofed config to recover.

## HP 15-f008cl, AMD A6-5200 (Kabini), from 2026-11-01

| | |
|---|---|
| CPU | A6-5200: 4 × Jaguar, 2.0 GHz. AVX, BMI1, MOVBE; no AVX2/BMI2/FMA/RDRAND (same OpenCore choices as the A4-5000: `HfsPlusLegacy.efi`, 4 cores) |
| GPU | Radeon HD 8400: **1002:9830 rev 0x00**, subsystem 103C:216F; 512 MB carve-out, BAR0 256 MB at `0xE0000000` |
| Model, firmware | HP 15 Notebook PC, board 2330, Insyde **F.21 (2014-09-25)**, UEFI 2.3.1 without a MAT |
| Memory | 1 × 4 GB (single channel) |
| ACPI | **has VFCT** (the VBIOS comes through ACPI, so KaberiRed's VFCT path applies), plus WDAT/WDRT (watchdog tables); EC0 under `\_SB.PCI0.LPC0`; same `HPET._STA` as the A4-5000 (switches the HPET off unless `_OSI` answers a Windows version) |
| Display | **LVDS** panel (not eDP), HDMI, VGA |
| Input | PS/2 keyboard, Synaptics PS/2 touchpad (fw 7.5) |
| Network | Realtek RTL8188EE Wi-Fi (10EC:8179, **no macOS driver**), Realtek RTL8106E Ethernet (10EC:8136, no prebuilt driver) |
| Other | FCH xHCI 1022:7814 + EHCI/OHCI as on the A4-5000; RTS5229 card reader (10EC:5229) |

EFI: `python scripts/build_efi_hp.py ../KaberiRed-RE/efi-hp15f --machine=hp15-f008cl ...`. Under Linux (`radeon`) the
GPU comes up fully (kabini microcode, 512 MB VRAM).

### Install log
- **2026-11-01, first EFI (4 cores, `-krplatprobe`):** the same freeze as the A4-5000, ~2 s in (`mbinit`, after
  `AppleUSBLegacyRoot`): `KRP 0` printed at t=250 ms and no `KRP 1`. KaberiRed found the iGPU (9830, VFCT VBIOS
  injected). The probe line read **`hpet=ffffffffffffffff`**: the HPET doesn't decode under macOS. Cause: both laptops'
  DSDTs set `OSTB` from `_OSI("Windows 20xx")`, macOS answers only "Darwin", and `HPET._STA` then clears the FCH's HPET
  enable (PM 0x50, `HPAD &= ~0x1F`). Linux and Windows answer the Windows strings and keep it. (The A4-5000's probe
  showed the HPET off too; the earlier note here that OSTB defaults to 0x61 was wrong.) Next test: `SSDT-XOSI` with
  the `_OSI` to `XOSI` rename, now in every build: the probe's `hpet=` field shows whether it landed.
- **`SSDT-XOSI`:** landed (the probe's `hpet=0/0`, no longer all ones) but the same freeze: not the HPET.
- **Probe with a spin wait (no sleeping):** `KRP` went on to line 185 (47 s) after the boot's own output stopped. CPU 0,
  kept busy by the probe, stayed alive: its APIC timer (vector 0xDD, ~10 ms one-shot) kept firing and being re-armed,
  nothing stuck in IRR/ISR, `smm=0/0/0` throughout (no firmware time). So the CPUs that idle don't come back, which
  fits every earlier run, including the A4-5000's single-CPU freezes with a dead keyboard. Next test: `--nohlt` again,
  now with the probe's `idle=` field (`nop` once KaberiRed reads `sti; nop` in `machine_idle`) proving it landed.
- **`--nohlt` (HP 15-f008cl):** the OpenCore log confirms it applied (`machine_idle: sti; hlt -> sti; nop … replace
  count - 1`), and the boot froze the same way: **not idle/HLT**. (The probe's `idle=` read `?`: Lilu couldn't solve
  `_machine_idle` in Recovery.) What fits every run: one CPU stuck with interrupts off, so the timers queued on it
  never fire and the threads waiting on them never wake. Next test: `krprobepanic=30`, a deliberate panic from the
  probe (alive through the freeze); on the way into the debugger XNU names CPUs that don't answer its IPI.

## MSI A78M-E35 V2 desktop, AMD A6-7400K (Kaveri), from 2026-11-15

The first Kaveri machine. Steamroller is family 15h, so it should boot on the same AMD_Vanilla path as the Excavator
laptops, which the Kabini (Jaguar) boot freeze above can't.

| | |
|---|---|
| CPU | A6-7400K: 2 × Steamroller, 3.5 GHz (family 15h model 30h stepping 1). AVX, FMA, SSE4.2, F16C, BMI1, XSAVEOPT; **no AVX2, BMI2 or RDRAND**: Ventura and later need CryptexFixup and the plain `x86_64` dyld cache; `HfsPlusLegacy.efi` |
| GPU | Radeon R5: **1002:1315 rev 0x00**, subsystem 1462:7721; GCN 2 gfx700, DCE 8.1. Linux (`radeon`, 6.17): kaveri microcode, all 8 rings and IB tests pass, UVD 1.64, VCE 40.2.2, DPM on, MSI |
| Board, firmware | MSI A78M-E35 V2 (MS-7721), AMI BIOS **V6.5 (2016-08-10)**, UEFI 2.3.1, no Memory Attributes Table |
| Memory | 16 GB DDR3, 128-bit; **1 GB carve-out**, which with 16 GB installed lies above 4 GB physical (usable RAM ends at `0x43EFFFFFF`) |
| GPU BARs | BAR0 256 MB at `0xC0000000` (smaller than the carve-out), doorbells 8 MB at `0xD0000000`, MMIO (BAR5) 256 KB at `0xFEB00000`, ROM at `0xC0000` |
| **No ACPI VFCT table** | the VBIOS (62,976 bytes, subsystem 1462:7721) is only in the legacy ROM shadow: UnifiedRed needs KaberiRed's `VBIOS::injectFromShadow` fallback |
| VBIOS tables | every command table Apple's AMDSupport uses is at a revision it handles: DIGxEncoderControl 1.4, DIG1TransmitterControl 1.5, SelectCRTC_Source 1.2, SetPixelClock 1.6, AdjustDisplayPll 1.3, EnableSpreadSpectrumOnPPLL 1.3, ProcessAuxChannelTransaction 1.2 (Apple: 4–5, 5–6, 2–3, 5–7, 3, 3, 2) |
| Displays | DVI-D (UNIPHY2, HPD3), HDMI (UNIPHY, HPD1), VGA through a **NUTMEG DP-to-VGA bridge** on UNIPHY3 (ExternalEncoderControl 2.3, which Apple never calls: VGA is not expected to work). Use HDMI or DVI |
| ACPI | no EC; GPU node `\_SB.PCI0.VGA`; LPC `\_SB.PCI0.SBRG`; `HPET._STA` switches the HPET off unless `_OSI` answers Windows 2006 or later (`SSDT-XOSI-A78M`); IVRS (AMD IOMMU) present; no `_UPC`, so no USB map from ACPI |
| Other devices | FCH xHCI 1022:7814 ×2 (00:10.0, 00:10.1), EHCI/OHCI 1022:7808/7807/7809, SATA AHCI 1022:7801, HD Audio 1022:780D, HDMI audio 1002:1308, Realtek RTL8111 Ethernet 10EC:8168 |

Linux dump: `CarrizoRed-RE/EFI/MSI A78M-E35 V2 (A6-7400K)/` (its decompiled ACPI tables in `Support files/acpi-dsl/`).
The register dump is missing (neither `umr` nor amdgpu's debugfs: the live session ran `radeon`); UnifiedRed's first
report fills it in.

EFI: `python scripts/build_efi_a78m.py "<machine folder>/Support files/efi-build" --fchxhcifix`: macOS Sequoia,
iMac19,1, 2 cores, Lilu, VirtualSMC, RestrictEvents, CryptexFixup, RealtekRTL8111, VoodooPS2 (keyboard), FCHXHCIFix
(matched to 7814), SSDT-EC-USBX, SSDT-XOSI-A78M, the iGPU's `vendor-id` overridden so Apple's AMDSupport leaves it
alone. BIOS: IOMMU off, Secure Boot off, CSM off. Install from a **USB 2.0 port** (EHCI, native) and over Ethernet.

### Install log
- **2026-11-15, Monterey 12.7.6 from an offline installer:** installs and boots on the firmware framebuffer
  (`vendor-id` FFFF0000). Setup Assistant goes grey after the country screen (no acceleration). USB: low-speed mouse
  works on the black ports (OHCI), the full-speed keyboard doesn't (EHCI "persistent enumeration failures"); the blue
  ports enumerate at 480 Mbps only. A boot from the blue ports once stopped at `validate_root_image`.
- **2026-11-15, `--bonaire` (UnifiedRed 7a6188d):** AMD8000Controller takes over the display (HDMI): picture.
- **2026-11-15, `--spoof` (UnifiedRed 88c98a2, DCE 11.2 → DCE 8 translation, IH off, `-crnoaccel`):** AMD9500Controller
  takes over the display: picture, first try.
- Getting logs off the machine is the open problem: no keyboard in the installer's Terminal, `dmpstore` to the USB
  stick freezes, and a panic restarts instead of staying on screen.

### 2026-11-21: A8-7680 (desktop Carrizo) in the same board
The A6-7400K was swapped for an **A8-7680**, a Carrizo die on FM2+ (4 Excavator cores, Radeon R7, GCN 3), so this board
now tests UnifiedRed's Carrizo path, not GCN 2. Excavator has AVX2 and RDRAND, so it needs no CryptexFixup and uses
Apple's `HfsPlus.efi`, like the D509BA. The Monterey 12.7.6 install on the disk stays. EFI:
`python scripts/build_efi_a78m.py "<A8-7680 folder>/Support files/efi-build" --cpu=a8-7680 --unifiedred --spoof
[--accel] --nopanicat --fchxhcifix` (`--bonaire` is refused: it is GCN 2 only). Built from 778c4c5:
`usb-display` (`-crnoaccel`) and `usb-accel`, in `CarrizoRed-RE/EFI/MSI A78M-E35 V2 (A8-7680)/Support files/efi-build/`.
