# LP10 `adbd` repair: implementation and limits

The Detailed-with-Repair profile includes an opt-in SSH repair for one LP10
`/usr/bin/adbd` build. It ports the cleanup change from [AOSP commit
`3abd31d`](https://android.googlesource.com/platform/system/core/+/3abd31d).
In the affected implementation, `remote_close` closes `t->fd`, the internal
socketpair descriptor at offset `+0x18`. Generic transport removal also closes
that descriptor. The upstream change instead closes `t->sfd`, the network
socket at `+0x54`, after checking that it is not already `-1`, then stores
`-1` before closing it.

The wrong descriptor close is confirmed in the two examined LP10 firmware
binaries. The binaries were byte-identical on firmware `AR241CE_8530.23.2`.
The relationship between this cleanup defect and the separate observed
`CNXN` invalid-pointer crash is not proven. The repair targets the confirmed
cleanup defect; it should not be described as a proven cure for every cause
of TCP/5555 loss.

## Firmware-specific binary port

The patcher is a binary port for one ARM ELF32 executable, not a vendor-source
rebuild or a verbatim application of the AOSP commit. It accepts only the
supported original binary SHA-256:

```text
764d9681ddd37d2f3318207067268b22b47b745703cb4daf17f9500ce0e2475b
```

It redirects `remote_close` at VMA `0x17e2c` to a 28-byte ARM routine placed in
the executable code cave at VMA `0x27578`, then extends the executable ELF
segment. The routine reads `t->sfd`, returns if it is already `-1`, marks it
closed, and tail-calls `close`. All other bytes are preserved. The generated
candidate SHA-256 is:

```text
db97220b5d69c8fe131e1cad69fabb44872abb206610d8887a728003b32669a7
```

`repair_support/build_adbd_remote_close.py` contains the deterministic patch
logic and rejects a binary whose hash, ELF architecture, function bytes, or
segment layout do not match. The captured vendor executable is deliberately
not distributed here. The development script therefore needs an authorized
copy placed at its documented private evidence path to regenerate and verify
the candidate; the installed repair reads the stock executable from the LP10
over SSH and patches that in memory.

## What Apply ADB fix does

The repair is manual and runs only from the Detailed-with-Repair profile. It
checks the source binary and running processes, verifies local and device-side
backups, writes and verifies a separate candidate under `/tmp`, switches the
running process, and checks that TCP/5555 responds. It reports progress and
attempts to restart the unchanged stock executable if candidate startup or
status verification fails. It never replaces `/usr/bin/adbd`.

This is temporary: rebooting the LP10 clears the candidate from `/tmp` and its
normal startup launches the manufacturer's stock binary again. A manufacturer
firmware update may also change the binary; the exact-hash check refuses
unsupported builds. No persistence hook or modified firmware image is
included. The repair requires SSH access as root, accepts the key presented
by the player, and uses the known factory credential by default. Run it only
on a trusted LAN and review the risks before enabling Detailed-with-Repair.

The owner observed stable metadata and control while the patch ran on TestLP10-1, including track skipping and EQ changes. That test is evidence for
that tested unit/build, not broad validation across all LP10 devices or
firmware versions. Players without the patch must continue to handle metadata
failure normally.
