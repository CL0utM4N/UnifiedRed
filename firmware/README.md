# Firmware

`amdgpu/stoney_uvd.bin` is AMD's UVD firmware for Stoney (version 1.43), unmodified, from
[linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/amdgpu)
(SHA-256 `f7474c77bd185b6c2df3c7d24cb656e8e0edb24f1e296cb09447e3e41501a498`). It is redistributed under AMD's licence
in [LICENSE.amdgpu](LICENSE.amdgpu), which applies to it; the rest of UnifiedRed's licence does not.

UnifiedRed embeds it as is and, with `-cruvd` on Stoney, loads it in place of the Polaris UVD firmware Apple's driver
uses: Stoney's UVD 6.2 runs Apple's firmware but doesn't decode correctly with it. The kext only reads the standard
container header (the same `common_firmware_header` Linux's amdgpu reads) to find the microcode and its size.

Copyright (C) 2024 Advanced Micro Devices, Inc. All rights reserved. The full permission notice and disclaimers are in
[LICENSE.amdgpu](LICENSE.amdgpu).
