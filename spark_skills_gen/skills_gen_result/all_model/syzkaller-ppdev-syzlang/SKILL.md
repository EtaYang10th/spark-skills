---
title: Writing Syzkaller Syzlang Device Descriptions
category: syzkaller-syzlang
tags: [syzkaller, syzlang, linux, fuzzing, ioctl, device-driver]
difficulty: intermediate
success_rate: high
---

# Writing Syzkaller Syzlang Device Descriptions

## Overview

This skill covers writing syzkaller syzlang descriptions for Linux device drivers — specifically the `.txt` description file and its companion `.const` file. The task pattern is: given a Linux device (e.g., `/dev/parport*`), produce a complete syzlang description covering all ioctls, resources, structs, and flags, then verify with `make descriptions` and `make all`.

---

## HIGH-LEVEL WORKFLOW

### Step 1: Explore the Syzkaller Repo Structure

Before writing anything, understand the conventions by reading existing device descriptions.

```bash
# List existing device description files
ls /opt/syzkaller/sys/linux/dev_*.txt | head -20

# List existing .const files
ls /opt/syzkaller/sys/linux/*.const | head -10

# Read a simple, complete example (floppy is a good reference)
cat /opt/syzkaller/sys/linux/dev_floppy.txt
cat /opt/syzkaller/sys/linux/dev_floppy.txt.const
```

Key things to note from the reference:
- How `resource` types are declared
- How `syz_open_dev` is used for `/dev/X#` devices
- How ioctl directions (`_IOW`, `_IOR`, `_IOWR`, no-arg) map to syzlang argument patterns
- How flag sets are declared with `=` syntax
- How structs are declared with `{}`

### Step 2: Find and Read the Kernel Header

Locate the device's primary header file to enumerate all ioctls and constants.

```bash
# Find the header
find / -name "ppdev.h" 2>/dev/null | head -5
find / -name "parport.h" 2>/dev/null | head -5

# Read both headers fully
cat /usr/include/linux/ppdev.h
cat /usr/include/linux/parport.h
```

From the header, extract:
- All `#define PP*` ioctl macros (note `_IO`, `_IOW`, `_IOR`, `_IOWR` direction)
- All flag/constant `#define` values
- All struct definitions used as ioctl arguments

### Step 3: Compute Ioctl Numbers

Ioctl numbers are NOT the same as the macro names — they are encoded integers. Compute them with Python using the Linux ioctl encoding formula.

```python
#!/usr/bin/env python3
"""
Linux ioctl number computation.
Formula: (dir << 30) | (type << 8) | (nr) | (size << 16)
  dir:  0=_IO, 1=_IOW, 2=_IOR, 3=_IOWR  (note: _IOW=write FROM user, _IOR=read TO user)
  type: the 'magic' byte (e.g., ord('p') for ppdev)
  nr:   the ioctl number within the type
  size: sizeof the argument type
"""

import struct

def _IOC(dir_, type_, nr, size):
    return (dir_ << 30) | (size << 16) | (ord(type_) << 8) | nr

def _IO(type_, nr):       return _IOC(0, type_, nr, 0)
def _IOW(type_, nr, sz):  return _IOC(1, type_, nr, sz)
def _IOR(type_, nr, sz):  return _IOC(2, type_, nr, sz)
def _IOWR(type_, nr, sz): return _IOC(3, type_, nr, sz)

# Size constants for common types
INT  = 4   # sizeof(int)
UINT = 4   # sizeof(unsigned int)
PTR  = 8   # sizeof(void*) on 64-bit — but use struct size, not pointer size

# For ppdev (magic = 'p' = 0x70):
ioctls = {
    # _IO ioctls (no argument)
    'PPCLAIM':    _IO('p', 0x8b),
    'PPRELEASE':  _IO('p', 0x8c),
    'PPYIELD':    _IO('p', 0x8d),
    'PPEXCL':     _IO('p', 0x8f),

    # _IOW ioctls (write: user -> kernel)
    'PPSETMODE':   _IOW('p', 0x80, INT),
    'PPWCONTROL':  _IOW('p', 0x84, 1),   # unsigned char
    'PPFCONTROL':  _IOW('p', 0x8e, 8),   # struct ppdev_frob_struct (2 bytes, padded to 8)
    'PPWDATA':     _IOW('p', 0x86, 1),   # unsigned char
    'PPDATADIR':   _IOW('p', 0x90, INT),
    'PPNEGOT':     _IOW('p', 0x91, INT),
    'PPWCTLONIRQ': _IOW('p', 0x92, 1),   # unsigned char
    'PPSETPHASE':  _IOW('p', 0x94, INT),
    'PPSETTIME':   _IOW('p', 0x96, 16),  # struct timeval (16 bytes on 64-bit)
    'PPSETFLAGS':  _IOW('p', 0x9b, INT),

    # _IOR ioctls (read: kernel -> user)
    'PPRSTATUS':   _IOR('p', 0x81, 1),   # unsigned char
    'PPRCONTROL':  _IOR('p', 0x83, 1),   # unsigned char
    'PPRDATA':     _IOR('p', 0x85, 1),   # unsigned char
    'PPCLRIRQ':    _IOR('p', 0x93, INT),
    'PPGETTIME':   _IOR('p', 0x95, 16),  # struct timeval
    'PPGETMODES':  _IOR('p', 0x97, UINT),
    'PPGETMODE':   _IOR('p', 0x98, INT),
    'PPGETPHASE':  _IOR('p', 0x99, INT),
    'PPGETFLAGS':  _IOR('p', 0x9a, INT),
}

for name, val in sorted(ioctls.items()):
    print(f"{name} = {val}")
```

Run this to get the exact integer values needed for the `.const` file.

### Step 4: Write the `.txt` Description File

Structure of a complete device description:

```
# Copyright notice (optional)

include <linux/ppdev.h>
include <linux/parport.h>

# 1. Resource declaration
resource fd_ppdev[fd]

# 2. Opener syscall
syz_open_dev$ppdev(dev ptr[in, string["/dev/parport#"]], flags flags[open_flags]) fd_ppdev

# 3. Ioctls — grouped by direction
#    No-arg ioctls: ioctl(fd, CMD)
ioctl$PPCLAIM(fd fd_ppdev, cmd const[PPCLAIM])
ioctl$PPRELEASE(fd fd_ppdev, cmd const[PPRELEASE])

#    Write ioctls (_IOW): ioctl(fd, CMD, ptr[in, TYPE])
ioctl$PPSETMODE(fd fd_ppdev, cmd const[PPSETMODE], mode ptr[in, flags[ieee1284_mode, int32]])
ioctl$PPWCONTROL(fd fd_ppdev, cmd const[PPWCONTROL], ctrl ptr[in, flags[parport_control, int8]])

#    Read ioctls (_IOR): ioctl(fd, CMD, ptr[out, TYPE])
ioctl$PPRSTATUS(fd fd_ppdev, cmd const[PPRSTATUS], status ptr[out, int8])
ioctl$PPGETMODE(fd fd_ppdev, cmd const[PPGETMODE], mode ptr[out, int32])

# 4. Structs
ppdev_frob_struct {
    mask    int8
    val     int8
}

# 5. Flag sets
ieee1284_mode = IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE, IEEE1284_MODE_COMPAT, IEEE1284_MODE_ECP, IEEE1284_MODE_ECPRLE, IEEE1284_MODE_ECPSWE, IEEE1284_MODE_EPP, IEEE1284_MODE_EPPSL, IEEE1284_MODE_EPPSWE, IEEE1284_MODE_BECP, IEEE1284_ADDR, IEEE1284_DATA, IEEE1284_EXT_LINK, IEEE1284_DEVICEID

ieee1284_phase = IEEE1284_PH_FWD_IDLE, IEEE1284_PH_FWD_DATA, IEEE1284_PH_FWD_UNKNOWN, IEEE1284_PH_HBUSY_DNA, IEEE1284_PH_REV_IDLE, IEEE1284_PH_HBUSY_DAVAIL, IEEE1284_PH_REV_DATA, IEEE1284_PH_REV_UNKNOWN, IEEE1284_PH_ECP_SETUP, IEEE1284_PH_ECP_FWD_TO_REV, IEEE1284_PH_ECP_REV_IDLE, IEEE1284_PH_ECP_REV_TO_FWD, IEEE1284_PH_ECP_DIR_UNKNOWN

parport_control = PARPORT_CONTROL_STROBE, PARPORT_CONTROL_AUTOFD, PARPORT_CONTROL_INIT, PARPORT_CONTROL_SELECT

ppdev_flags = PP_FASTWRITE, PP_FASTREAD, PP_W91284PIC
```

**Complete ppdev example** — write this to `/opt/syzkaller/sys/linux/dev_ppdev.txt`:

```
# Copyright 2024 syzkaller project authors. All rights reserved.
# Use of this source code is governed by Apache 2 LICENSE that can be found in the LICENSE file.

include <linux/ppdev.h>
include <linux/parport.h>

resource fd_ppdev[fd]

syz_open_dev$ppdev(dev ptr[in, string["/dev/parport#"]], flags flags[open_flags]) fd_ppdev

ioctl$PPCLAIM(fd fd_ppdev, cmd const[PPCLAIM])
ioctl$PPRELEASE(fd fd_ppdev, cmd const[PPRELEASE])
ioctl$PPYIELD(fd fd_ppdev, cmd const[PPYIELD])
ioctl$PPEXCL(fd fd_ppdev, cmd const[PPEXCL])

ioctl$PPSETMODE(fd fd_ppdev, cmd const[PPSETMODE], mode ptr[in, flags[ieee1284_mode, int32]])
ioctl$PPWCONTROL(fd fd_ppdev, cmd const[PPWCONTROL], ctrl ptr[in, flags[parport_control, int8]])
ioctl$PPFCONTROL(fd fd_ppdev, cmd const[PPFCONTROL], frob ptr[in, ppdev_frob_struct])
ioctl$PPWDATA(fd fd_ppdev, cmd const[PPWDATA], data ptr[in, int8])
ioctl$PPDATADIR(fd fd_ppdev, cmd const[PPDATADIR], dir ptr[in, int32])
ioctl$PPNEGOT(fd fd_ppdev, cmd const[PPNEGOT], mode ptr[in, flags[ieee1284_mode, int32]])
ioctl$PPWCTLONIRQ(fd fd_ppdev, cmd const[PPWCTLONIRQ], ctrl ptr[in, int8])
ioctl$PPSETPHASE(fd fd_ppdev, cmd const[PPSETPHASE], phase ptr[in, flags[ieee1284_phase, int32]])
ioctl$PPSETTIME(fd fd_ppdev, cmd const[PPSETTIME], tv ptr[in, timeval])
ioctl$PPSETFLAGS(fd fd_ppdev, cmd const[PPSETFLAGS], flags ptr[in, flags[ppdev_flags, int32]])

ioctl$PPRSTATUS(fd fd_ppdev, cmd const[PPRSTATUS], status ptr[out, int8])
ioctl$PPRCONTROL(fd fd_ppdev, cmd const[PPRCONTROL], ctrl ptr[out, int8])
ioctl$PPRDATA(fd fd_ppdev, cmd const[PPRDATA], data ptr[out, int8])
ioctl$PPCLRIRQ(fd fd_ppdev, cmd const[PPCLRIRQ], count ptr[out, int32])
ioctl$PPGETTIME(fd fd_ppdev, cmd const[PPGETTIME], tv ptr[out, timeval])
ioctl$PPGETMODES(fd fd_ppdev, cmd const[PPGETMODES], modes ptr[out, int32])
ioctl$PPGETMODE(fd fd_ppdev, cmd const[PPGETMODE], mode ptr[out, int32])
ioctl$PPGETPHASE(fd fd_ppdev, cmd const[PPGETPHASE], phase ptr[out, int32])
ioctl$PPGETFLAGS(fd fd_ppdev, cmd const[PPGETFLAGS], flags ptr[out, int32])

ppdev_frob_struct {
	mask	int8
	val	int8
}

ieee1284_mode = IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE, IEEE1284_MODE_COMPAT, IEEE1284_MODE_ECP, IEEE1284_MODE_ECPRLE, IEEE1284_MODE_ECPSWE, IEEE1284_MODE_EPP, IEEE1284_MODE_EPPSL, IEEE1284_MODE_EPPSWE, IEEE1284_MODE_BECP, IEEE1284_ADDR, IEEE1284_DATA, IEEE1284_EXT_LINK, IEEE1284_DEVICEID

ieee1284_phase = IEEE1284_PH_FWD_IDLE, IEEE1284_PH_FWD_DATA, IEEE1284_PH_FWD_UNKNOWN, IEEE1284_PH_HBUSY_DNA, IEEE1284_PH_REV_IDLE, IEEE1284_PH_HBUSY_DAVAIL, IEEE1284_PH_REV_DATA, IEEE1284_PH_REV_UNKNOWN, IEEE1284_PH_ECP_SETUP, IEEE1284_PH_ECP_FWD_TO_REV, IEEE1284_PH_ECP_REV_IDLE, IEEE1284_PH_ECP_REV_TO_FWD, IEEE1284_PH_ECP_DIR_UNKNOWN

parport_control = PARPORT_CONTROL_STROBE, PARPORT_CONTROL_AUTOFD, PARPORT_CONTROL_INIT, PARPORT_CONTROL_SELECT

ppdev_flags = PP_FASTWRITE, PP_FASTREAD, PP_W91284PIC
```

### Step 5: Write the `.const` File

The `.const` file maps symbolic names to their integer values. It must:
- Start with `arches = 386, amd64` (or whatever arches are relevant)
- List every constant referenced in the `.txt` file, alphabetically sorted
- Include `__NR_ioctl` with arch-specific values

**Format:**
```
# Code generated by syz-sysgen. DO NOT EDIT.
arches = 386, amd64
CONSTANT_NAME = integer_value
...
__NR_ioctl = 54, amd64:16
```

**Complete ppdev `.const` file** — write to `/opt/syzkaller/sys/linux/dev_ppdev.txt.const`:

```
# Code generated by syz-sysgen. DO NOT EDIT.
arches = 386, amd64
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
IEEE1284_PH_ECP_DIR_UNKNOWN = 12
IEEE1284_PH_ECP_FWD_TO_REV = 9
IEEE1284_PH_ECP_REV_IDLE = 10
IEEE1284_PH_ECP_REV_TO_FWD = 11
IEEE1284_PH_ECP_SETUP = 8
IEEE1284_PH_FWD_DATA = 1
IEEE1284_PH_FWD_IDLE = 0
IEEE1284_PH_FWD_UNKNOWN = 3
IEEE1284_PH_HBUSY_DAVAIL = 5
IEEE1284_PH_HBUSY_DNA = 2
IEEE1284_PH_REV_DATA = 6
IEEE1284_PH_REV_IDLE = 4
IEEE1284_PH_REV_UNKNOWN = 7
PARPORT_CONTROL_AUTOFD = 2
PARPORT_CONTROL_INIT = 4
PARPORT_CONTROL_SELECT = 8
PARPORT_CONTROL_STROBE = 1
PPCLAIM = 28811
PPCLRIRQ = 2147774611
PPDATADIR = 1074032784
PPEXCL = 28815
PPFCONTROL = 1073901710
PPGETFLAGS = 2147774618
PPGETMODE = 2147774616
PPGETMODES = 2147774615
PPGETPHASE = 2147774617
PPGETTIME = 2148561045
PPNEGOT = 1074032785
PPRCONTROL = 2147577987
PPRDATA = 2147577989
PPRELEASE = 28812
PPRSTATUS = 2147577985
PPSETFLAGS = 1074032795
PPSETMODE = 1074032768
PPSETPHASE = 1074032788
PPSETTIME = 1074819222
PPWCONTROL = 1073836164
PPWCTLONIRQ = 1073836178
PPWDATA = 1073836166
PPYIELD = 28813
PP_FASTREAD = 8
PP_FASTWRITE = 4
PP_W91284PIC = 16
__NR_ioctl = 54, amd64:16
```

### Step 6: Verify with Make

```bash
cd /opt/syzkaller

# Step 1: validate descriptions parse correctly
make descriptions 2>&1

# Step 2: full build to catch type errors and missing constants
make all TARGETOS=linux TARGETARCH=amd64 2>&1
```

Both must exit 0. If either fails, read the error carefully — it will point to the exact line and symbol.

---

## COMMON PITFALLS

### 1. Redefining types already in `sys.txt`

`timeval` is already defined globally in `/opt/syzkaller/sys/linux/sys.txt`. Do NOT redeclare it in your device file. Use it directly:

```
# WRONG — causes "type timeval redeclared" error
timeval {
    sec     int64
    usec    int64
}

# CORRECT — just reference it
ioctl$PPSETTIME(fd fd_ppdev, cmd const[PPSETTIME], tv ptr[in, timeval])
```

Check for other globally-defined types before declaring your own:
```bash
grep -n "^timeval\|^timespec\|^sockaddr" /opt/syzkaller/sys/linux/sys.txt | head -20
```

### 2. Wrong ioctl direction (`_IOW` vs `_IOR`)

The naming is from the kernel's perspective, but the syzlang direction is from the user's perspective:
- `_IOW` (kernel reads from user) → `ptr[in, ...]` in syzlang
- `_IOR` (kernel writes to user) → `ptr[out, ...]` in syzlang
- `_IOWR` → `ptr[inout, ...]` in syzlang
- `_IO` (no data) → no third argument

```
# _IOW: user writes data to kernel
ioctl$PPSETMODE(fd fd_ppdev, cmd const[PPSETMODE], mode ptr[in, int32])

# _IOR: kernel writes data back to user
ioctl$PPGETMODE(fd fd_ppdev, cmd const[PPGETMODE], mode ptr[out, int32])
```

### 3. Incorrect ioctl number computation

The ioctl number encodes direction, type byte, number, and argument size. Always compute with Python rather than guessing:

```python
def _IOC(dir_, type_, nr, size):
    # dir: 0=_IO, 1=_IOW, 2=_IOR, 3=_IOWR
    return (dir_ << 30) | (size << 16) | (ord(type_) << 8) | nr

# Verify against kernel header values if possible
# e.g., PPCLAIM = _IO('p', 0x8b) = (0<<30)|(0<<16)|(0x70<<8)|0x8b = 0x708b = 28811
assert _IOC(0, 'p', 0x8b, 0) == 28811
```

### 4. Missing `__NR_ioctl` in `.const`

Every `.const` file that describes ioctls must include the syscall number for `ioctl`. For x86/x86_64:

```
__NR_ioctl = 54, amd64:16
```

The format `54, amd64:16` means: default value 54 (for 386), but 16 for amd64.

### 5. Wrong `arches` line

The `arches` line must be the first non-comment line and must match the architectures you're targeting:

```
# CORRECT
arches = 386, amd64

# WRONG — missing space after comma, or wrong arch names
arches=386,amd64
arches = x86, x86_64
```

### 6. Struct size mismatches in ioctl encoding

When computing ioctl numbers for structs, use the actual C struct size (with padding), not the sum of field sizes:

```python
# ppdev_frob_struct has 2 bytes (mask + val) but may be padded
# Check with: python3 -c "import ctypes; ..."
# Or just read the kernel header comment / use the value from the header directly

# For PPFCONTROL = _IOW('p', 0x8e, struct ppdev_frob_struct)
# struct ppdev_frob_struct { unsigned char mask; unsigned char val; } = 2 bytes
# But the ioctl macro in the header uses sizeof, so trust the computed value
```

### 7. Flag set syntax errors

Flag sets use `=` not `:=` and items are comma-separated without trailing comma:

```
# CORRECT
ieee1284_mode = IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE, IEEE1284_MODE_COMPAT

# WRONG — trailing comma
ieee1284_mode = IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE,

# WRONG — using := 
ieee1284_mode := IEEE1284_MODE_NIBBLE, IEEE1284_MODE_BYTE
```

### 8. Not checking for existing similar descriptions

Before writing from scratch, check if a partial description already exists:

```bash
grep -r "ppdev\|parport\|PPCLAIM" /opt/syzkaller/sys/linux/ 2>/dev/null
```

---

## QUICK REFERENCE: Syzlang Type Mapping

| C type | Syzlang type |
|--------|-------------|
| `int` / `unsigned int` | `int32` |
| `unsigned char` | `int8` |
| `struct timeval` | `timeval` (from sys.txt) |
| pointer to read | `ptr[in, TYPE]` |
| pointer to write | `ptr[out, TYPE]` |
| pointer to read+write | `ptr[inout, TYPE]` |
| enum/flags value | `flags[FLAG_SET, int32]` |
| exact constant | `const[CONSTANT_NAME]` |

## QUICK REFERENCE: Ioctl Direction Encoding

| Macro | Dir bits | Syzlang ptr direction |
|-------|----------|----------------------|
| `_IO(t,n)` | 0 | no pointer arg |
| `_IOW(t,n,sz)` | 1 | `ptr[in, ...]` |
| `_IOR(t,n,sz)` | 2 | `ptr[out, ...]` |
| `_IOWR(t,n,sz)` | 3 | `ptr[inout, ...]` |

## QUICK REFERENCE: `syz_open_dev` Pattern

For devices at `/dev/NAME#` (numbered, e.g., `/dev/parport0`):

```
syz_open_dev$DEVNAME(dev ptr[in, string["/dev/NAME#"]], flags flags[open_flags]) fd_DEVNAME
```

For devices at a fixed path (e.g., `/dev/ppp`):

```
openat$DEVNAME(fd const[AT_FDCWD], file ptr[in, string["/dev/ppp"]], flags flags[open_flags], mode const[0]) fd_DEVNAME
```
