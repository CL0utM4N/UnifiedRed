#!/usr/bin/env python3
"""
Build an OpenCore EFI for the MSI A78M-E35 V2 (A6-7400K, Kaveri) GCN 2 test desktop, macOS Sequoia.

    python scripts/build_efi_a78m.py <work dir> [--unifiedred [--nopanicat] [--spoof|--bonaire [--accel]]] [--fchxhcifix]
                                                [--args="..."] [--oclog] [--debugoc]

    --unifiedred  add UnifiedRed (Debug build) with -crdbg and crpanicat=60, the deliberate report panic
    --nopanicat   with --unifiedred: no crpanicat=60
    --spoof       with --unifiedred: device-id 0x67EF (Polaris 11) so Apple's drivers attach; -crnoaccel unless --accel
    --bonaire     with --unifiedred, Monterey and older: device-id 0x665C (Bonaire, R7 260X) so AMD8000Controller, Apple's
                  own DCE 8 display controller, drives the display; -crnoaccel unless --accel
    --fchxhcifix  github.com/CL0utM4N/FCHXHCIFix, its IOPCIPrimaryMatch extended to this board's xHCI (1022:7814)
    --debugoc     OpenCore's DEBUG binaries (unpacked/OpenCore-<ver>-DEBUG/); --oclog writes OpenCore's log to the stick

<work dir> must contain:
    unpacked/OpenCore-<ver>-RELEASE/        OpenCorePkg release zip, extracted
    unpacked/<Kext>-<ver>-RELEASE/          Lilu, VirtualSMC, RestrictEvents, CryptexFixup, VoodooPS2Controller
    unpacked/RealtekRTL8111-*/              github.com/Mieze/RTL8111_driver_for_OS_X release, extracted
    unpacked/UnifiedRed/                    (--unifiedred) UnifiedRed.kext from CI (UnifiedRed-*-Debug.zip)
    unpacked/FCHXHCIFix/                    (--fchxhcifix) FCHXHCIFix.kext
    downloads/AMD_Vanilla-patches.plist     github.com/AMD-OSX/AMD_Vanilla patches.plist
    downloads/HfsPlusLegacy.efi             github.com/acidanthera/OcBinaryData Drivers/HfsPlusLegacy.efi (no RDRAND)
    acpi/SSDT-EC-USBX-LAPTOP.aml            compiled from docs/opencore/SSDT-EC-USBX-LAPTOP.dsl (iasl)
    acpi/SSDT-XOSI-A78M.aml                 compiled from docs/gcn2/opencore/SSDT-XOSI-A78M.dsl (iasl)
    recovery/com.apple.recovery.boot/       (optional) Sequoia Recovery from OpenCore's macrecovery.py
                                            (-b Mac-937A206F2EE63C01 -m 00000000000000000 download)
and receives:
    usb/                                    copy its contents (EFI + com.apple.recovery.boot) to a FAT32 USB stick

SMBIOS serials are generated with OpenCore's macserial on the first build and kept in <work dir>/smbios.plist, so
rebuilding keeps the same identity. They never go into the repository.

Choices follow the Dortania OpenCore Install Guide for AMD family 15h, adjusted from the board's Linux dump
(docs/gcn2/TEST_MACHINES.md):
- CPU: A6-7400K, 2 Steamroller cores (family 15h model 30h). AVX, FMA, SSE4.2, but no AVX2, BMI2 or RDRAND: Ventura
  and later need CryptexFixup (the non-AVX2 dyld cache), and Apple's HfsPlus.efi needs RDRAND (HfsPlusLegacy.efi).
- Firmware: AMI UEFI 2.3.1 (BIOS V6.5, 2016) without a Memory Attributes Table; the GPU's BARs sit below 4 GB.
- ACPI: no EC (desktop: SSDT-EC-USBX's do-nothing EC goes under \\_SB.PCI0.SBRG), no VFCT table (UnifiedRed reads
  the VBIOS from the legacy ROM), HPET._STA switches the HPET off unless _OSI answers a Windows version (SSDT-XOSI-A78M).
- Devices: Realtek RTL8111 Ethernet (RealtekRTL8111, also for Recovery), PS/2 keyboard/mouse nodes, two FCH xHCI
  controllers (1022:7814) and EHCI/OHCI. No _UPC in the DSDT, so no USB map yet: install from a USB 2.0 port.
- BIOS: IOMMU off, Secure Boot off, CSM off.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

CORE_COUNT = 2                      # A6-7400K: 2 Steamroller cores
SMBIOS_MODEL = "iMac19,1"           # Sequoia-supported, Polaris graphics. Not MacPro6,1/7,1 or iMacPro1,1:
# AppleIntelMCEReporter matches those board-ids and panics on AMD CPUs (D509BA, 2026-10-09).
BOOT_ARGS = "-v keepsyms=1 debug=0x100 npci=0x3000 msgbuf=1048576 revpatch=sbvm,cpuname"
# npci=0x3000: AMD systems without an "Above 4G decoding" option. msgbuf: 1 MB kernel log, so boot messages survive.

KEXTS = [  # (bundle path inside its release folder, executable, comment, MinKernel)
    ("Lilu-*/Lilu.kext", "Lilu", "Patch engine", ""),
    ("VirtualSMC-*/Kexts/VirtualSMC.kext", "VirtualSMC", "SMC emulator", ""),
    ("RestrictEvents-*/RestrictEvents.kext", "RestrictEvents", "CPU name, misc. fixes", ""),
    ("CryptexFixup-*/CryptexFixup.kext", "CryptexFixup", "Non-AVX2 CPU: Ventura+ dyld cache", "22.0.0"),
    ("RealtekRTL8111-*/Release/RealtekRTL8111.kext", "RealtekRTL8111", "Realtek RTL8111 Ethernet", ""),
    ("VoodooPS2Controller-*/VoodooPS2Controller.kext", "VoodooPS2Controller", "PS/2 controller", ""),
    ("VoodooPS2Controller-*/VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard.kext", "VoodooPS2Keyboard",
     "PS/2 keyboard", ""),
]
UNIFIEDRED_KEXT = ("UnifiedRed/UnifiedRed.kext", "UnifiedRed", "UnifiedRed (Lilu plugin, iGPU)", "")
FCHXHCIFIX_KEXT = ("FCHXHCIFix/FCHXHCIFix.kext", "FCHXHCIFix", "USB: service the FCH xHCI if its interrupts are lost", "")

DRIVERS = ["OpenRuntime.efi", "HfsPlusLegacy.efi", "ResetNvramEntry.efi"]

SSDTS = {  # compiled from docs/opencore or docs/gcn2/opencore/<name>.dsl into <work dir>/acpi/
    "SSDT-EC-USBX-LAPTOP.aml": "Fake EC + USBX (no real EC on this board; EC under SBRG)",
    "SSDT-XOSI-A78M.aml": "_OSI answers as Windows 10 (with the DSDT's _OSI to XOSI rename): keeps the HPET on",
}

# Polaris 11 (Baffin): in the IOPCIMatch lists of AMD9500Controller, AMDRadeonX4000 and HWServices. CAIL reads the
# real device ID (0x1315) from config space and takes its Spectre (Kaveri) path (docs/OLDER_APUS.md).
SPOOF_DEVICE_ID = bytes.fromhex("EF670000")
BONAIRE_DEVICE_ID = bytes.fromhex("5C660000")
IGPU = "PciRoot(0x0)/Pci(0x1,0x0)"

# AppleUSBXHCI::reset() waits only 100 ms for USBCMD.HCRST to clear; the D509BA's FCH xHCI needs ~105 ms (see
# build_efi_d509ba.py). Same patch here: harmless if this board's controllers reset faster.
XHCI_RESET_PATCH = {
    "Arch": "x86_64", "Base": "__ZN12AppleUSBXHCI5resetEv", "Comment": "AppleUSBXHCI reset() timeout 100 -> 1000 ms",
    "Count": 1, "Enabled": True, "Find": bytes.fromhex("bf64000000be40420f00"),
    "Identifier": "com.apple.driver.usb.AppleUSBXHCI", "Limit": 0x400, "Mask": b"", "MaxKernel": "",
    "MinKernel": "20.0.0", "Replace": bytes.fromhex("bfe8030000be40420f00"), "ReplaceMask": b"", "Skip": 0,
}


def die(msg):
    sys.exit(f"error: {msg}")


def one(base, pattern):
    hits = sorted(base.glob(pattern))
    if len(hits) != 1:
        die(f"expected exactly one match for {pattern} in {base}, found {len(hits)}")
    return hits[0]


def amd_vanilla_patches(path):
    patches = plistlib.load(open(path, "rb"))["Kernel"]["Patch"]
    cores = 0
    for p in patches:
        if "cpuid_cores_per_package" in p.get("Comment", ""):
            rep = bytearray(p["Replace"])
            rep[1] = CORE_COUNT
            p["Replace"] = bytes(rep)
            cores += 1
    if cores != 4:
        die(f"expected 4 core-count patches in AMD_Vanilla, found {cores}")
    return patches


def smbios(work, oc):
    store = work / "smbios.plist"
    if store.exists():
        return plistlib.load(open(store, "rb"))
    macserial = oc / "Utilities" / "macserial" / ("macserial.exe" if os.name == "nt" else "macserial")
    out = subprocess.run([str(macserial), "-m", SMBIOS_MODEL, "-g", "-n", "1"], capture_output=True, text=True,
                         check=True).stdout.strip()
    serial, mlb = [s.strip() for s in out.split("|")]
    data = {"SystemProductName": SMBIOS_MODEL, "SystemSerialNumber": serial, "MLB": mlb,
            "SystemUUID": str(uuid.uuid4()).upper(), "ROM": os.urandom(6)}
    plistlib.dump(data, open(store, "wb"))
    return data


def main():
    opts = sys.argv[1:]
    args = [a for a in opts if not a.startswith("--")]
    with_ur = "--unifiedred" in opts
    with_bonaire = "--bonaire" in opts
    with_spoof = "--spoof" in opts or with_bonaire
    if "--spoof" in opts and with_bonaire:
        die("--spoof and --bonaire are alternatives")
    if with_spoof and not with_ur:
        die("--spoof/--bonaire need --unifiedred (it supplies the VBIOS Apple's drivers need)")
    if len(args) != 1:
        sys.exit(__doc__)
    work = Path(args[0]).resolve()
    unpacked = work / "unpacked"
    oc = one(unpacked, "OpenCore-*-RELEASE")
    ocbin = one(unpacked, "OpenCore-*-DEBUG") if "--debugoc" in opts else oc
    ssdts = [work / "acpi" / n for n in SSDTS]
    for ssdt in ssdts:
        if not ssdt.exists():
            die(f"{ssdt} missing: compile {ssdt.stem}.dsl (docs/opencore or docs/gcn2/opencore) with iasl first")

    usb = work / "usb"
    if usb.exists():
        shutil.rmtree(usb)
    efi = usb / "EFI"
    shutil.copytree(ocbin / "X64" / "EFI" / "BOOT", efi / "BOOT")
    ocdir = efi / "OC"
    for sub in ("ACPI", "Drivers", "Kexts", "Tools"):
        (ocdir / sub).mkdir(parents=True)
    shutil.copy2(ocbin / "X64" / "EFI" / "OC" / "OpenCore.efi", ocdir)
    shutil.copytree(oc / "X64" / "EFI" / "OC" / "Resources", ocdir / "Resources")
    for ssdt in ssdts:
        shutil.copy2(ssdt, ocdir / "ACPI")
    for d in DRIVERS:
        src = ocbin / "X64" / "EFI" / "OC" / "Drivers" / d
        if not src.exists():
            src = work / "downloads" / d
            if not src.exists():
                die(f"{d} missing: put it in {work / 'downloads'}")
        shutil.copy2(src, ocdir / "Drivers")
    shutil.copy2(ocbin / "X64" / "EFI" / "OC" / "Tools" / "OpenShell.efi", ocdir / "Tools")
    recovery = work / "recovery" / "com.apple.recovery.boot"
    if recovery.exists():
        shutil.copytree(recovery, usb / recovery.name)

    cfg = plistlib.load(open(oc / "Docs" / "Sample.plist", "rb"))

    # --- ACPI -------------------------------------------------------------------------------------
    cfg["ACPI"]["Add"] = [{"Comment": SSDTS[n], "Enabled": True, "Path": n} for n in SSDTS]
    cfg["ACPI"]["Delete"] = []
    cfg["ACPI"]["Patch"] = [{
        "Base": "", "BaseSkip": 0, "Comment": "_OSI to XOSI (DSDT)", "Count": 0, "Enabled": True,
        "Find": b"_OSI", "Limit": 0, "Mask": b"", "OemTableId": bytes(8), "Replace": b"XOSI", "ReplaceMask": b"",
        "Skip": 0, "TableLength": 0, "TableSignature": b"DSDT"}]
    cfg["ACPI"]["Quirks"].update(FadtEnableReset=False, NormalizeHeaders=False, RebaseRegions=False,
                                 ResetHwSig=False, ResetLogoStatus=True, SyncTableIds=False)

    # --- Booter: AMD, UEFI 2.3.1 firmware without a Memory Attributes Table ----------------------
    cfg["Booter"]["MmioWhitelist"] = []
    cfg["Booter"]["Patch"] = []
    cfg["Booter"]["Quirks"].update(
        AvoidRuntimeDefrag=True, DevirtualiseMmio=False, EnableSafeModeSlide=True, EnableWriteUnprotector=True,
        ProvideCustomSlide=True, RebuildAppleMemoryMap=False, SetupVirtualMap=True, SyncRuntimePermissions=False,
        ProtectUefiServices=False, ResizeAppleGpuBars=-1, AllowRelocationBlock=False, DiscardHibernateMap=False,
        ForceExitBootServices=False, ProvideMaxSlide=0)

    # --- DeviceProperties ---------------------------------------------------------------------------
    # Without UnifiedRed's spoof, keep Apple's AMDSupport (it matches any display-class 1002:xxxx device) off the iGPU:
    # IOPCIMatch compares the vendor-id property. The class code stays, so the firmware framebuffer keeps working.
    if with_spoof:
        gpu = {"device-id": BONAIRE_DEVICE_ID if with_bonaire else SPOOF_DEVICE_ID}
    else:
        gpu = {"vendor-id": bytes.fromhex("FFFF0000")}
    cfg["DeviceProperties"]["Add"] = {IGPU: gpu}
    cfg["DeviceProperties"]["Delete"] = {}

    # --- Kernel -----------------------------------------------------------------------------------
    adds = []
    kexts = KEXTS + ([FCHXHCIFIX_KEXT] if "--fchxhcifix" in opts else []) + ([UNIFIEDRED_KEXT] if with_ur else [])
    for pattern, exe, comment, minkernel in kexts:
        src = one(unpacked, pattern)
        if "/PlugIns/" not in pattern:
            shutil.copytree(src, ocdir / "Kexts" / src.name)
        rel = pattern.split("/", 1)[1]
        for prefix in ("Kexts/", "Release/"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
        adds.append({"Arch": "Any", "BundlePath": rel, "Comment": comment, "Enabled": True,
                     "ExecutablePath": f"Contents/MacOS/{exe}", "MaxKernel": "", "MinKernel": minkernel,
                     "PlistPath": "Contents/Info.plist"})
    if "--fchxhcifix" in opts:
        info = ocdir / "Kexts" / "FCHXHCIFix.kext" / "Contents" / "Info.plist"
        plist = plistlib.load(open(info, "rb"))
        for personality in plist["IOKitPersonalities"].values():
            # 1.1.0 also has FCHHubSleep, which matches USB hubs, not PCI.
            if "IOPCIPrimaryMatch" in personality and "0x78141022" not in personality["IOPCIPrimaryMatch"]:
                personality["IOPCIPrimaryMatch"] += " 0x78141022"
        plistlib.dump(plist, open(info, "wb"))
    cfg["Kernel"]["Add"] = adds
    cfg["Kernel"]["Block"] = []
    cfg["Kernel"]["Force"] = []
    cfg["Kernel"]["Patch"] = amd_vanilla_patches(work / "downloads" / "AMD_Vanilla-patches.plist") + [XHCI_RESET_PATCH]
    cfg["Kernel"]["Emulate"].update(DummyPowerManagement=True, Cpuid1Data=b"", Cpuid1Mask=b"")
    cfg["Kernel"]["Quirks"].update(
        AppleCpuPmCfgLock=False, AppleXcpmCfgLock=False, AppleXcpmExtraMsrs=False, DisableIoMapper=False,
        DisableLinkeditJettison=True, LapicKernelPanic=False, PanicNoKextDump=True, PowerTimeoutKernelPanic=True,
        ProvideCurrentCpuInfo=True, XhciPortLimit=False, ThirdPartyDrives=False)
    cfg["Kernel"]["Scheme"].update(FuzzyMatch=True, KernelArch="x86_64", KernelCache="Auto")

    # --- Misc -------------------------------------------------------------------------------------
    cfg["Misc"]["Boot"].update(HideAuxiliary=False, PickerMode="Builtin", ShowPicker=True, Timeout=5,
                               PollAppleHotKeys=True)
    cfg["Misc"]["Debug"].update(AppleDebug=True, ApplePanic=True, DisableWatchDog=True, Target=3)
    if "--oclog" in opts:
        cfg["Misc"]["Debug"].update(Target=0x41, DisplayLevel=0x80000042)
    cfg["Misc"]["Security"].update(AllowSetDefault=True, BlacklistAppleUpdate=True, ExposeSensitiveData=6,
                                   ScanPolicy=0, SecureBootModel="Disabled", Vault="Optional")
    cfg["Misc"]["Entries"] = []
    cfg["Misc"]["Tools"] = [{"Arguments": "", "Auxiliary": True, "Comment": "UEFI shell", "Enabled": True,
                             "Flavour": "OpenShell:UEFIShell:Shell", "FullNvramAccess": False,
                             "Name": "UEFI Shell", "Path": "OpenShell.efi", "RealPath": False, "TextMode": False}]

    # --- NVRAM ------------------------------------------------------------------------------------
    boot = cfg["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
    boot["boot-args"] = BOOT_ARGS
    if with_ur:
        boot["boot-args"] += " -crdbg" + ("" if "--nopanicat" in opts else " crpanicat=60")
        if with_spoof and "--accel" not in opts:
            boot["boot-args"] += " -crnoaccel"
    for arg in opts:
        if arg.startswith("--args="):
            boot["boot-args"] += " " + arg[len("--args="):]
    boot["csr-active-config"] = bytes(4)
    boot["prev-lang:kbd"] = "en-US:0"
    cfg["NVRAM"]["WriteFlash"] = True

    # --- PlatformInfo -----------------------------------------------------------------------------
    cfg["PlatformInfo"]["Generic"].update(smbios(work, oc))
    cfg["PlatformInfo"]["UpdateSMBIOSMode"] = "Create"

    # --- UEFI -------------------------------------------------------------------------------------
    cfg["UEFI"]["Drivers"] = [{"Arguments": "", "Comment": "", "Enabled": True, "LoadEarly": False, "Path": d,
                               "HideVerbose": False} for d in DRIVERS]
    cfg["UEFI"]["Output"].update(ProvideConsoleGop=True, Resolution="Max")
    cfg["UEFI"]["Quirks"].update(ReleaseUsbOwnership=True, RequestBootVarRouting=True, UnblockFsConnect=False)

    plistlib.dump(cfg, open(ocdir / "config.plist", "wb"), sort_keys=False)
    print(f"EFI written to {usb} (boot-args: {boot['boot-args']})")
    print(f"SMBIOS {SMBIOS_MODEL}, serials stored in {work / 'smbios.plist'}")
    if not recovery.exists():
        print(f"note: no Recovery in {recovery.parent}; the stick boots only an installer or an existing install")


if __name__ == "__main__":
    main()
