//
//  kern_uvdfw.cpp
//  UnifiedRed
//
//  AMD's Stoney UVD firmware (linux-firmware amdgpu/stoney_uvd.bin 1.43, unmodified; licence: firmware/LICENSE.amdgpu),
//  embedded as is. The path is relative to the repository root, where make runs.
//

#include <stdint.h>
#include <stddef.h>

__asm__(".section __DATA,__const\n"
		".p2align 4\n"
		"_crStoneyUvdFw:\n"
		".incbin \"firmware/amdgpu/stoney_uvd.bin\"\n"
		"_crStoneyUvdFwEnd:\n"
		".byte 0\n"
		".text\n");

extern "C" const uint8_t crStoneyUvdFw[] __asm__("_crStoneyUvdFw");
extern "C" const uint8_t crStoneyUvdFwEnd[] __asm__("_crStoneyUvdFwEnd");

const uint8_t *stoneyUvdFirmware(size_t &size) {
	size = static_cast<size_t>(crStoneyUvdFwEnd - crStoneyUvdFw);
	return crStoneyUvdFw;
}
