---
title: "Writing Syzkaller Syzlang Descriptions for Linux Device Drivers"
category: syzkaller-syzlang
domain: kernel-fuzzing
tags:
  - syzkaller
  - syzlang
  - linux-kernel
  - device-drivers
  - ioctl
  - fuzzing
---

# Writing Syzkaller Syzlang Descriptions for Linux Device Drivers

## Overview

Syzkaller is a coverage-guided kernel fuzzer. To fuzz a device driver, you must describe its syscall interface in **syzlang** — syzkaller's domain-specific language. This skill covers the full workflow: reading kernel headers, computing ioctl numbers, writing `.txt` (syzlang) and `.txt.const` (constants) files, and verifying the build.

This skill is applicable to any Linux character device driver that exposes ioctls via `/dev/*`.

---

## High-Level Workflow

1. **Identify the kernel headers** that define the driver's user-space API (ioctl commands, structs, flags). Typically found under `include/uapi/linux/` or `include/linux/`.

2. **Catalog every ioctl** — extract the macro name, direction (`_IO`, `_IOR`, `_IOW`, `_IOWR`), type byte, number, and data type. Count them to ensure completeness.

3. **Catalog all structs and flags** used as ioctl arguments.

4. **Study existing syzlang examples** in `/opt/syzkaller/sys/linux/dev_*.txt` to match the project's conventions for resources, openers, ioctls, structs, and flag groups.

5. **Write the `.txt` syzlang file** with includes, resource definition, device opener, ioctl descriptions, struct definitions, and flag groups.

6. **Compute ioctl constant values** using the Linux `_IO`/`_IOR`/`_IOW`/`_IOWR` encoding formula. Handle arch-specific sizes (e.g., `struct timeval` differs between amd64 and 386).

7. **Write the `.txt.const` file** with `arches = amd64, 386` and all numeric constant values.

8. **Build and verify** with `make descriptions` then `make all TARGETOS=linux TARGETARCH=amd64`.

---

## Step 1: Find and Read Kernel Headers

Locate the relevant headers on the build system:

```bash
find / -name "ppdev.h" -type f 2>/dev/null
find / -name "parport.h" -type f 2>/dev/null
```

Read them and extract every `#define` that uses `_IO`, `_IOR`, `_IOW`, or `_IOWR`, plus all struct definitions and flag constants.

Key patterns to look for:

```c
// Simple ioctl, no data argument
#define PPCLAIM    _IO(PP_IOCTL, 0x8b)

// Write ioctl (userspace → kernel), takes a data argument
#define PPSETMODE  _IOW(PP_IOCTL, 0x80, int)

// Read ioctl (kernel → userspace), returns data
#define PPGETMODE  _IOR(PP_IOCTL, 0x88, int)
```

The direction in the macro name tells you the syzlang `ptr` direction:
- `_IO` → no data arg (or `const[0]`)
- `_IOW` → `ptr[in, ...]` (user writes to kernel)
- `_IOR` → `ptr[out, ...]` (kernel writes to user)
- `_IOWR` → `ptr[inout, ...]`

---

## Step 2: Compute Ioctl Numbers

Linux ioctl numbers are encoded as 32-bit values:

```
bits 31-30: direction (00=none, 01=write, 10=read, 11=read+write)
bits 29-16: size of argument type
bits 15-8:  type (magic byte)
bits 7-0:   number (command index)
```

Direction encoding (from kernel perspective — opposite of user perspective):
- `_IO`   → `0` (no direction)
- `_IOW`  → `1` (user writes → kernel reads)
- `_IOR`  → `2` (kernel writes → user reads)
- `_IOWR` → `3` (both)

**Critical**: The "direction" in the ioctl encoding is from the *kernel's* perspective. `_IOW` means the kernel *reads* from the user buffer, so direction bits = `01`. `_IOR` means the kernel *writes* to the user buffer, so direction bits = `10`.

### Python Script for Computing Ioctl Numbers

```python
import struct

def _IOC(dir_val, type_byte, nr, size):
    """Compute Linux ioctl number."""
    return (dir_val << 30) | (size << 16) | (type_byte << 8) | nr

def _IO(type_byte, nr):
    return _IOC(0, type_byte, nr, 0)

def _IOW(type_byte, nr, size):
    return _IOC(1, type_byte, nr, size)

def _IOR(type_byte, nr, size):
    return _IOC(2, type_byte, nr, size)

def _IOWR(type_byte, nr, size):
    return _IOC(3, type_byte, nr, size)

# Common type sizes
INT_SIZE = 4          # sizeof(int) — same on amd64 and 386
CHAR_SIZE = 1         # sizeof(unsigned char)

# struct timeval: arch-dependent!
TIMEVAL_SIZE_64 = 16  # amd64: two 8-byte fields (time_t + suseconds_t)
TIMEVAL_SIZE_32 = 8   # 386: two 4-byte fields

# ppdev type byte
PP_IOCTL = 0x70

# Example: compute all ppdev ioctls
ioctls = {
    "PPSETMODE":    _IOW(PP_IOCTL, 0x80, INT_SIZE),
    "PPRSTATUS":    _IOR(PP_IOCTL, 0x81, CHAR_SIZE),
    "PPWSTATUS":    None,  # doesn't exist for ppdev
    "PPRCONTROL":   _IOR(PP_IOCTL, 0x83, CHAR_SIZE),
    "PPWCONTROL":   _IOW(PP_IOCTL, 0x84, CHAR_SIZE),
    "PPFCONTROL":   _IOW(PP_IOCTL, 0x8e, 2),  # struct ppdev_frob_struct, 2 bytes
    "PPRDDATA":     _IOR(PP_IOCTL, 0x85, CHAR_SIZE),
    "PPWDATA":      _IOW(PP_IOCTL, 0x86, CHAR_SIZE),
    "PPCLAIM":      _IO(PP_IOCTL, 0x8b),
    "PPRELEASE":    _IO(PP_IOCTL, 0x8c),
    "PPYIELD":      _IO(PP_IOCTL, 0x8d),
    "PPEXCL":       _IO(PP_IOCTL, 0x8f),
    "PPDATADIR":    _IOW(PP_IOCTL, 0x90, INT_SIZE),
    "PPNEGOT":      _IOW(PP_IOCTL, 0x91, INT_SIZE),
    "PPWCTLONIRQ":  _IOW(PP_IOCTL, 0x92, CHAR_SIZE),
    "PPCLRIRQ":     _IOR(PP_IOCTL, 0x93, INT_SIZE),
    "PPSETPHASE":   _IOW(PP_IOCTL, 0x94, INT_SIZE),
    "PPGETPHASE":   _IOR(PP_IOCTL, 0x99, INT_SIZE),
    "PPGETMODES":   _IOR(PP_IOCTL, 0x97, INT_SIZE),
    "PPGETMODE":    _IOR(PP_IOCTL, 0x98, INT_SIZE),
    "PPGETFLAGS":   _IOR(PP_IOCTL, 0x9a, INT_SIZE),
    "PPSETFLAGS":   _IOW(PP_IOCTL, 0x9b, INT_SIZE),
    # Arch-dependent ioctls
    "PPGETTIME_64": _IOR(PP_IOCTL, 0x95, TIMEVAL_SIZE_64),
    "PPGETTIME_32": _IOR(PP_IOCTL, 0x95, TIMEVAL_SIZE_32),
    "PPSETTIME_64": _IOW(PP_IOCTL, 0x96, TIMEVAL_SIZE_64),
    "PPSETTIME_32": _IOW(PP_IOCTL, 0x96, TIMEVAL_SIZE_32),
}

for name, val in ioctls.items():
    if val is not None:
        print(f"{name} = {val}")
```

### Arch-Specific Constants

When a struct has different sizes on different architectures (like `struct timeval`), the `.txt.const` file uses the format:

```
PPGETTIME = 2148561045, 386:2148036757
PPSETTIME = 1074819222, 386:1074294934
```

The first value is the amd64 default; the `386:` prefix overrides for 32-bit x86.

---

## Step 3: Study Existing Syzlang Conventions

Before writing your own descriptions, read 2-3 existing `dev_*.txt` files to learn the conventions:

```bash
# List existing device descriptions
ls /opt/syzkaller/sys/linux/dev_*.txt | head -20

# Good examples to study
cat /opt/syzkaller/sys/linux/dev_sg.txt       # SCSI generic — ioctls + structs
cat /opt/syzkaller/sys/linux/dev_loop.txt     # loop device — simpler
```

Key conventions to observe:

- **Resource types**: `resource fd_mydev[fd]` — wraps a file descriptor
- **Device opener**: `syz_open_dev$mydev(dev ptr[in, string["/dev/mydev#"]], id intptr, flags flags[open_flags]) fd_mydev`
- **Ioctl naming**: `ioctl$MYIOCTL(fd fd_mydev, cmd const[MYIOCTL], arg ptr[in, mytype])`
- **No-arg ioctls**: `ioctl$MYIOCTL(fd fd_mydev, cmd const[MYIOCTL])` — omit the `arg` parameter entirely for `_IO` ioctls
- **Struct definitions**: Use tabs for indentation, one field per line
- **Flag groups**: `my_flags = FLAG_A, FLAG_B, FLAG_C`
- **Reuse existing types**: Check `sys/linux/sys.txt` for types like `timeval`, `timespec` before defining your own

```bash
# Check if timeval is already defined
cd /opt/syzkaller && grep -n "^timeval" sys/linux/sys.txt
```

---

## Step 4: Write the Syzlang `.txt` File

### Structure

```
# Copyright notice (optional)
# Description comment

include <linux/header1.h>
include <linux/header2.h>

resource fd_mydev[fd]

syz_open_dev$mydev(dev ptr[in, string["/dev/mydev#"]], id intptr, flags flags[open_flags]) fd_mydev

# Group ioctls logically

ioctl$IOCTL_NAME(fd fd_mydev, cmd const[IOCTL_NAME], arg ptr[in, type])
...

# Struct definitions
my_struct {
	field1	type1
	field2	type2
}

# Flag groups
my_flags = FLAG_A, FLAG_B, FLAG_C
```

### Direction Rules

| Ioctl Macro | Syzlang `ptr` direction | Meaning |
|-------------|------------------------|---------|
| `_IOW`      | `ptr[in, ...]`         | User passes data to kernel |
| `_IOR`      | `ptr[out, ...]`        | Kernel returns data to user |
| `_IOWR`     | `ptr[inout, ...]`      | Bidirectional |
| `_IO`       | No `arg` parameter     | No data transfer |

### Type Mapping

| C Type | Syzlang Type |
|--------|-------------|
| `int` | `int32` |
| `unsigned int` | `int32` (syzkaller uses signed by convention) |
| `unsigned char` | `int8` |
| `char` | `int8` |
| `struct timeval` | `timeval` (from sys.txt) |
| Custom struct | Define inline |
| Flags field | `flags[flag_group_name, int32]` |

### Using Flags

When an ioctl takes a value that is a bitmask of flags, use the `flags` type:

```
ioctl$PPSETMODE(fd fd_ppdev, cmd const[PPSETMODE], arg ptr[in, flags[ieee1284_modes, int32]])
```

When an ioctl takes a plain integer (not flags), use the raw type:

```
ioctl$PPDATADIR(fd fd_ppdev, cmd const[PPDATADIR], arg ptr[in, int32])
```

---

## Step 5: Write the `.txt.const` File

### Format

```
# Code generated by syz-sysgen. DO NOT EDIT.
arches = amd64, 386
CONSTANT_NAME = decimal_value
ARCH_SPECIFIC = amd64_value, 386:i386_value
```

Rules:
- **All values are decimal** (not hex)
- **One constant per line**, alphabetical order is conventional but not required
- **Arch-specific values** use the format `default_value, 386:override_value`
- **Include ALL constants** referenced in the `.txt` file: ioctl command names, flag values, mode values
- The `arches` line must list all target architectures

### What Constants to Include

Every symbolic name used in the `.txt` file that isn't a built-in syzlang keyword needs a constant entry:
- All ioctl command names (e.g., `PPCLAIM`, `PPSETMODE`)
- All flag values (e.g., `PP_FASTWRITE`, `IEEE1284_MODE_EPP`)
- Do NOT include type names like `fd_ppdev` or struct names

---

## Step 6: Build and Verify

```bash
cd /opt/syzkaller

# Step 1: Regenerate description bindings
make descriptions

# Step 2: Full build for target arch
make all TARGETOS=linux TARGETARCH=amd64
```

If `make descriptions` fails, the error usually points to a syntax error in your `.txt` file or a missing constant in your `.txt.const` file. Common errors:

- `unknown type X` — you referenced a type not defined in your file or in `sys.txt`
- `unknown const X` — missing from `.txt.const`
- Syntax errors — check tab vs space, missing braces, wrong `ptr` direction syntax

---

## Common Pitfalls

### 1. Wrong ioctl direction mapping
The most common mistake. `_IOW` means the user *writes* to the kernel, so syzlang uses `ptr[in, ...]`. `_IOR` means the kernel *writes* to the user, so syzlang uses `ptr[out, ...]`. Do NOT confuse these.

### 2. Forgetting arch-specific sizes
`struct timeval` is 16 bytes on amd64 (two `long` = 8 bytes each) but 8 bytes on 386 (two `long` = 4 bytes each). This changes the ioctl number. You must provide both values in the `.txt.const` file.

### 3. Including `arg` for `_IO` ioctls
Ioctls defined with `_IO()` (no data direction) should NOT have an `arg` parameter in syzlang. Just `ioctl$NAME(fd fd_mydev, cmd const[NAME])`.

### 4. Using hex values in `.txt.const`
All values must be decimal. Convert hex to decimal before writing.

### 5. Missing constants
Every symbolic name in the `.txt` file needs a corresponding entry in `.txt.const`. This includes flag values, not just ioctl names.

### 6. Redefining existing types
Check `sys/linux/sys.txt` and other files before defining types like `timeval`, `timespec`, `fd`, etc. Syzkaller will error on duplicate definitions.

### 7. Wrong struct size for ioctl computation
The `ppdev_frob_struct` is `{ uint8 mask; uint8 val; }` = 2 bytes. Using `sizeof(int)` = 4 would give the wrong ioctl number.

### 8. Forgetting the `#` in device path
`syz_open_dev` uses `#` as a placeholder for the device number: `"/dev/parport#"`. Without `#`, syzkaller can't enumerate device instances.

---

## Reference Implementation

This is a complete, self-contained implementation for the Linux ppdev (parallel port) driver. For a different driver, replace the header names, ioctl definitions, and constant values, but follow the same structure.

### File 1: `/opt/syzkaller/sys/linux/dev_ppdev.txt`

```syzlang
# Copyright 2024 syzkaller project authors. All rights reserved.
# Use of this source code is governed by Apache 2 LICENSE that can be found in the LICENSE file.

include <linux/ppdev.h>
include <linux/parport.h>

resource fd_ppdev[fd]

syz_open_dev$ppdev(dev ptr[in, string["/dev/parport#"]], id intptr, flags flags[open_flags]) fd_ppdev

ioctl$PPSETMODE(fd fd_ppdev, cmd const[PPSETMODE], arg ptr[in, flags[ieee1284_modes, int32]])
ioctl$PPGETMODE(fd fd_ppdev, cmd const[PPGETMODE], arg ptr[out, int32])
ioctl$PPGETMODES(fd fd_ppdev, cmd const[PPGETMODES], arg ptr[out, int32])

ioctl$PPSETPHASE(fd fd_ppdev, cmd const[PPSETPHASE], arg ptr[in, int32])
ioctl$PPGETPHASE(fd fd_ppdev, cmd const[PPGETPHASE], arg ptr[out, int32])

ioctl$PPRSTATUS(fd fd_ppdev, cmd const[PPRSTATUS], arg ptr[out, int8])
ioctl$PPRDATA(fd fd_ppdev, cmd const[PPRDATA], arg ptr[out, int8])
ioctl$PPRCONTROL(fd fd_ppdev, cmd const[PPRCONTROL], arg ptr[out, int8])

ioctl$PPWSTATUS(fd fd_ppdev, cmd const[PPWSTATUS], arg ptr[in, int8])
ioctl$PPWDATA(fd fd_ppdev, cmd const[PPWDATA], arg ptr[in, int8])
ioctl$PPWCONTROL(fd fd_ppdev, cmd const[PPWCONTROL], arg ptr[in, int8])

ioctl$PPFCONTROL(fd fd_ppdev, cmd const[PPFCONTROL], arg ptr[in, ppdev_frob_struct])

ioctl$PPDATADIR(fd fd_ppdev, cmd const[PPDATADIR], arg ptr[in, int32])
ioctl$PPNEGOT(fd fd_ppdev, cmd const[PPNEGOT], arg ptr[in, int32])
ioctl$PPWCTLONIRQ(fd fd_ppdev, cmd const[PPWCTLONIRQ], arg ptr[in, int8])
ioctl$PPCLRIRQ(fd fd_ppdev, cmd const[PPCLRIRQ], arg ptr[out, int32])

ioctl$PPGETTIME(fd fd_ppdev, cmd const[PPGETTIME], arg ptr[out, timeval])
ioctl$PPSETTIME(fd fd_ppdev, cmd const[PPSETTIME], arg ptr[in, timeval])

ioctl$PPGETFLAGS(fd fd_ppdev, cmd const[PPGETFLAGS], arg ptr[out, int32])
ioctl$PPSETFLAGS(fd fd_ppdev, cmd const[PPSETFLAGS], arg ptr[in, flags[ppdev_flags, int32]])

ioctl$PPCLAIM(fd fd_ppdev, cmd const[PPCLAIM])
ioctl$PPRELEASE(fd fd_ppdev, cmd const[PPRELEASE])
ioctl$PPYIELD(fd fd_ppdev, cmd const[PPYIELD])
ioctl$PPEXCL(fd fd_ppdev, cmd const[PPEXCL])

ppdev_frob_struct {
	mask	int8
	val	int8
}

ppdev_flags = PP_FASTWRITE, PP_FASTREAD, PP_W91284PIC
ieee1284_modes = IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE, IEEE1284_MODE_COMPAT, IEEE1284_MODE_BECP, IEEE1284_MODE_ECP, IEEE1284_MODE_ECPRLE, IEEE1284_MODE_ECPSWE, IEEE1284_MODE_EPP, IEEE1284_MODE_EPPSL, IEEE1284_MODE_EPPSWE, IEEE1284_DEVICEID, IEEE1284_EXT_LINK, IEEE1284_ADDR, IEEE1284_DATA
```

### File 2: `/opt/syzkaller/sys/linux/dev_ppdev.txt.const`

```
# Code generated by syz-sysgen. DO NOT EDIT.
arches = amd64, 386
PPCLAIM = 28811
PPCLRIRQ = 2147774611
PPDATADIR = 1074032784
PPEXCL = 28815
PPFCONTROL = 1073901710
PPGETFLAGS = 2147774618
PPGETMODE = 2147774616
PPGETMODES = 2147774615
PPGETPHASE = 2147774617
PPGETTIME = 2148561045, 386:2148036757
PPNEGOT = 1074032785
PPRCONTROL = 2147577987
PPRDATA = 2147577989
PPRELEASE = 28812
PPRSTATUS = 2147577985
PPSETFLAGS = 1074032795
PPSETMODE = 1074032768
PPSETPHASE = 1074032788
PPSETTIME = 1074819222, 386:1074294934
PPWCONTROL = 1073836164
PPWCTLONIRQ = 1073836178
PPWDATA = 1073836166
PPYIELD = 28813
PP_FASTREAD = 8
PP_FASTWRITE = 4
PP_W91284PIC = 16
IEEE1284_ADDR = 8192
IEEE1284_DATA = 0
IEEE1284_DEVICEID = 4
IEEE1284_EXT_LINK = 16384
IEEE1284_MODE_BECP = 512
IEEE1284_MODE_BYTE = 1
IEEE1284_MODE_COMPAT = 256
IEEE1284_MODE_ECP = 16
IEEE1284_MODE_ECPRLE = 48
IEEE1284_MODE_ECPSWE = 1024
IEEE1284_MODE_EPP = 64
IEEE1284_MODE_EPPSL = 2048
IEEE1284_MODE_EPPSWE = 4096
IEEE1284_MODE_NIBBLE = 0
```

### Constant Computation Script (for generating `.txt.const`)

```python
#!/usr/bin/env python3
"""
Compute ioctl numbers and constant values for a syzkaller .txt.const file.
Adapt the ioctl definitions for your target driver.
"""

def _IOC(direction, type_byte, nr, size):
    return (direction << 30) | (size << 16) | (type_byte << 8) | nr

def _IO(t, nr):        return _IOC(0, t, nr, 0)
def _IOW(t, nr, sz):   return _IOC(1, t, nr, sz)
def _IOR(t, nr, sz):   return _IOC(2, t, nr, sz)
def _IOWR(t, nr, sz):  return _IOC(3, t, nr, sz)

# === Driver-specific constants ===
PP_IOCTL = 0x70

# Type sizes
INT = 4
CHAR = 1
FROB = 2  # struct ppdev_frob_struct: 2 x uint8
TIMEVAL_64 = 16  # amd64
TIMEVAL_32 = 8   # 386

# === Ioctl definitions ===
# Format: (name, value) or (name, amd64_value, i386_value)
ioctls = [
    ("PPSETMODE",    _IOW(PP_IOCTL, 0x80, INT)),
    ("PPRSTATUS",    _IOR(PP_IOCTL, 0x81, CHAR)),
    ("PPRCONTROL",   _IOR(PP_IOCTL, 0x83, CHAR)),
    ("PPWCONTROL",   _IOW(PP_IOCTL, 0x84, CHAR)),
    ("PPRDATA",      _IOR(PP_IOCTL, 0x85, CHAR)),
    ("PPWDATA",      _IOW(PP_IOCTL, 0x86, CHAR)),
    ("PPCLAIM",      _IO(PP_IOCTL, 0x8b)),
    ("PPRELEASE",    _IO(PP_IOCTL, 0x8c)),
    ("PPYIELD",      _IO(PP_IOCTL, 0x8d)),
    ("PPFCONTROL",   _IOW(PP_IOCTL, 0x8e, FROB)),
    ("PPEXCL",       _IO(PP_IOCTL, 0x8f)),
    ("PPDATADIR",    _IOW(PP_IOCTL, 0x90, INT)),
    ("PPNEGOT",      _IOW(PP_IOCTL, 0x91, INT)),
    ("PPWCTLONIRQ",  _IOW(PP_IOCTL, 0x92, CHAR)),
    ("PPCLRIRQ",     _IOR(PP_IOCTL, 0x93, INT)),
    ("PPSETPHASE",   _IOW(PP_IOCTL, 0x94, INT)),
    ("PPGETMODES",   _IOR(PP_IOCTL, 0x97, INT)),
    ("PPGETMODE",    _IOR(PP_IOCTL, 0x98, INT)),
    ("PPGETPHASE",   _IOR(PP_IOCTL, 0x99, INT)),
    ("PPGETFLAGS",   _IOR(PP_IOCTL, 0x9a, INT)),
    ("PPSETFLAGS",   _IOW(PP_IOCTL, 0x9b, INT)),
]

# Arch-dependent ioctls
arch_ioctls = [
    ("PPGETTIME", _IOR(PP_IOCTL, 0x95, TIMEVAL_64), _IOR(PP_IOCTL, 0x95, TIMEVAL_32)),
    ("PPSETTIME", _IOW(PP_IOCTL, 0x96, TIMEVAL_64), _IOW(PP_IOCTL, 0x96, TIMEVAL_32)),
]

# === Flag and mode constants ===
flags = {
    "PP_FASTWRITE": 4,
    "PP_FASTREAD": 8,
    "PP_W91284PIC": 16,
    "IEEE1284_MODE_NIBBLE": 0,
    "IEEE1284_MODE_BYTE": 1,
    "IEEE1284_DEVICEID": 4,
    "IEEE1284_MODE_ECP": 16,
    "IEEE1284_MODE_ECPRLE": 48,
    "IEEE1284_MODE_EPP": 64,
    "IEEE1284_MODE_COMPAT": 256,
    "IEEE1284_MODE_BECP": 512,
    "IEEE1284_MODE_ECPSWE": 1024,
    "IEEE1284_MODE_EPPSL": 2048,
    "IEEE1284_MODE_EPPSWE": 4096,
    "IEEE1284_ADDR": 8192,
    "IEEE1284_EXT_LINK": 16384,
    "IEEE1284_DATA": 0,
}

# === Generate output ===
print("# Code generated by syz-sysgen. DO NOT EDIT.")
print("arches = amd64, 386")

for name, val in sorted(ioctls, key=lambda x: x[0]):
    print(f"{name} = {val}")

for name, val64, val32 in sorted(arch_ioctls, key=lambda x: x[0]):
    print(f"{name} = {val64}, 386:{val32}")

for name, val in sorted(flags.items()):
    print(f"{name} = {val}")
```

### Build Verification Script

```bash
#!/bin/bash
set -e

cd /opt/syzkaller

echo "=== Step 1: Regenerate descriptions ==="
make descriptions
echo "descriptions OK"

echo "=== Step 2: Full build ==="
make all TARGETOS=linux TARGETARCH=amd64
echo "build OK"

echo "=== All checks passed ==="
```

---

## Generalizing to Other Drivers

To apply this skill to a different driver (e.g., `/dev/video0`, `/dev/snd/controlC0`):

1. **Replace the header includes** with the driver's headers.
2. **Replace the resource name** (e.g., `fd_v4l2[fd]`).
3. **Replace the device path** in `syz_open_dev` (e.g., `"/dev/video#"`).
4. **Extract all ioctls** from the new header and compute their numbers.
5. **Define all structs** used as ioctl arguments. For complex structs with nested pointers, use syzlang's `ptr[in, ...]` syntax for pointer fields.
6. **Define all flag groups** used in the ioctls.
7. **Check for arch-dependent types** — any struct containing `long`, `size_t`, `time_t`, or pointers will have different sizes on 32-bit vs 64-bit.

### Checklist Before Submitting

- [ ] Every ioctl from the header is described (count them)
- [ ] `_IOW` → `ptr[in]`, `_IOR` → `ptr[out]`, `_IOWR` → `ptr[inout]`, `_IO` → no arg
- [ ] All structs referenced in ioctls are defined
- [ ] All flag groups referenced in ioctls are defined
- [ ] `.txt.const` has entries for every symbolic name used in `.txt`
- [ ] Arch-specific values are provided where struct sizes differ
- [ ] `make descriptions` passes
- [ ] `make all TARGETOS=linux TARGETARCH=amd64` passes
- [ ] No duplicate type definitions (check `sys.txt` first)