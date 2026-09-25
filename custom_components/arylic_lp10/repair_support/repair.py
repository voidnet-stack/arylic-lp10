"""Opt-in LP10 adbd repair for one supported firmware binary.

Requires Paramiko. This helper is intentionally separate from the HA polling
integration and never rewrites /usr/bin/adbd. The factory credential is
bundled by the owner's request; an interactive override is available.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import ipaddress
import os
from pathlib import Path
import socket
import sys
import time
from collections.abc import Callable

if __package__:
    from .build_adbd_remote_close import EXPECTED_SHA256, patch_bytes
    from .luci import StatusUnavailable, read_register
else:  # Direct execution by the standalone helper.
    from build_adbd_remote_close import EXPECTED_SHA256, patch_bytes
    from luci import StatusUnavailable, read_register


CANDIDATE_PATH = "/tmp/lp10-adbd-remote-close-candidate"
ORIGINAL_PATH = "/usr/bin/adbd"
REMOTE_BACKUP = "/tmp/lp10-adbd-original-backup"
PID_FILE = "/tmp/lp10-adbd-candidate.pid"
CANDIDATE_LOG = "/tmp/lp10-adbd-candidate.log"
STOCK_LOG = "/tmp/lp10-adbd-original-restored.log"
DEFAULT_SSH_PASSWORD = "123456"


def _private_ip(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError("Expected the LP10's private LAN IPv4 address")
    return str(address)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Device:
    def __init__(self, host: str, *, prompt_password: bool) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before using this helper") from exc
        self.host = _private_ip(host)
        connection = socket.create_connection((self.host, 22), timeout=10)
        self.transport = paramiko.Transport(connection)
        self.transport.start_client(timeout=10)
        # The LP10 regenerates its host key on reboot. Intentionally accept
        # the presented key on this private-IP connection for every session.
        password = (
            getpass.getpass(f"SSH password for root@{self.host}: ")
            if prompt_password else DEFAULT_SSH_PASSWORD
        )
        try:
            self.transport.auth_password("root", password)
        except paramiko.AuthenticationException as exc:
            self.transport.close()
            raise RuntimeError("SSH authentication failed") from exc
        finally:
            password = ""
        if not self.transport.is_authenticated():
            self.transport.close()
            raise RuntimeError("SSH authentication failed")

    def close(self) -> None:
        self.transport.close()

    def run(self, command: str) -> str:
        channel = self.transport.open_session(timeout=10)
        channel.settimeout(15)
        try:
            channel.exec_command(command)
            status = channel.recv_exit_status()
            stdout = channel.makefile("rb").read(65536).decode("utf-8", "replace").strip()
            stderr = channel.makefile_stderr("rb").read(65536).decode("utf-8", "replace").strip()
        finally:
            channel.close()
        if status:
            raise RuntimeError(f"Device command failed ({status}): {stderr or stdout}")
        return stdout

    def read(self, path: str) -> bytes:
        if path not in (ORIGINAL_PATH, CANDIDATE_PATH, REMOTE_BACKUP) and not (
            path.startswith("/proc/") and path.endswith("/exe")
            and path[6:-4].isdigit()
        ):
            raise ValueError("Unexpected device file path")
        channel = self.transport.open_session(timeout=10)
        channel.settimeout(15)
        try:
            channel.exec_command(f"cat {path}")
            content = channel.makefile("rb").read(512 * 1024 + 1)
            status = channel.recv_exit_status()
        finally:
            channel.close()
        if status or len(content) > 512 * 1024:
            raise RuntimeError("Could not read the expected device executable")
        return content

    def write(self, path: str, content: bytes) -> None:
        if path != CANDIDATE_PATH:
            raise ValueError("Unexpected staging path")
        channel = self.transport.open_session(timeout=10)
        channel.settimeout(15)
        try:
            channel.exec_command(f"umask 077; cat > {path}")
            channel.sendall(content)
            channel.shutdown_write()
            status = channel.recv_exit_status()
        finally:
            channel.close()
        if status:
            raise RuntimeError("Could not stage the candidate on the device")
        self.run(f"chmod 755 {path}")
        if _sha256(self.read(path)) != _sha256(content):
            raise RuntimeError("Staged candidate did not verify")

    def pids(self, path: str) -> list[int]:
        # Only fixed internal paths reach the remote shell.
        if path not in (ORIGINAL_PATH, CANDIDATE_PATH):
            raise ValueError("Unexpected process path")
        output = self.run(
            'for e in /proc/[0-9]*/exe; do '
            f'[ "$(readlink "$e" 2>/dev/null)" = "{path}" ] || continue; '
            'p=${e%/exe}; printf "%s " "${p#/proc/}"; done'
        )
        return [int(value) for value in output.split()]

    def listening(self) -> bool:
        return self.run(
            "awk 'NR > 1 && $2 ~ /:15B3$/ && $4 == \"0A\" {found=1} "
            "END {print found ? \"yes\" : \"no\"}' "
            "/proc/net/tcp /proc/net/tcp6"
        ) == "yes"


def _backup_original(original: bytes, host: str, backup_dir: Path | None = None) -> Path:
    directory = backup_dir or Path.home() / ".lp10-repair" / "backups" / host
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise RuntimeError("Could not secure the local adbd backup directory") from exc
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-adbd-original"
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(original)
    return path


def _preflight(device: Device) -> tuple[bytes, bytes]:
    original = device.read(ORIGINAL_PATH)
    if _sha256(original) != EXPECTED_SHA256:
        raise RuntimeError(
            "Unsupported /usr/bin/adbd hash. This firmware must be reviewed "
            "before repair; no process was changed."
        )
    mounts = device.run("cat /proc/mounts")
    tmp_mount = next((line for line in mounts.splitlines() if line.split()[1:2] == ["/tmp"]), "")
    if not tmp_mount or "noexec" in tmp_mount.split()[3].split(","):
        raise RuntimeError("/tmp is unavailable or mounted noexec")
    device.run("command -v nohup >/dev/null 2>&1")
    candidate = patch_bytes(original)
    return original, candidate


def _check_status(host: str) -> None:
    # A listening socket alone cannot show that the status service works.
    try:
        response = read_register(host, "LUCI_local -r 42", timeout=3)
    except StatusUnavailable as exc:
        raise RuntimeError("TCP/5555 did not return device status") from exc
    if "Data:" not in response:
        raise RuntimeError("TCP/5555 returned no device status")


def _start(
    device: Device, path: str, log: str, pid_file: str,
    progress: Callable[[str, str], None] | None = None,
) -> None:
    if path not in (ORIGINAL_PATH, CANDIDATE_PATH):
        raise ValueError("Unexpected executable path")
    device.run(f"nohup {path} </dev/null >{log} 2>&1 & echo $! >{pid_file}")
    last_failure: RuntimeError | None = None
    for attempt in range(1, 6):
        if progress:
            progress("verifying", f"Checking TCP/5555 status (attempt {attempt} of 5).")
        time.sleep(2)
        if not device.pids(path):
            last_failure = RuntimeError(f"{path} did not stay running")
            continue
        if not device.listening():
            last_failure = RuntimeError(f"{path} did not start listening on TCP/5555")
            continue
        try:
            _check_status(device.host)
        except RuntimeError as exc:
            last_failure = exc
            continue
        return
    raise last_failure or RuntimeError(f"{path} did not start")


def _stop(device: Device, path: str) -> None:
    for pid in device.pids(path):
        # Recheck /proc immediately before signalling to avoid a stale PID.
        current = device.run(f"readlink /proc/{pid}/exe 2>/dev/null || true")
        if current == path:
            device.run(f"kill {pid}")
    for _ in range(5):
        time.sleep(1)
        if not device.pids(path):
            return
    raise RuntimeError(f"{path} did not stop")


def inspect(device: Device) -> None:
    result = inspect_status(device)
    print(f"Device: {device.run('hostname')}")
    print(f"Stock binary SHA-256: {result['binary_sha256']}")
    print(f"Repair status: {result['status']}")
    print(f"Stock PIDs: {result['stock_pids']}")
    print(f"Patched PIDs: {result['patched_pids']}")
    print(f"TCP/5555 status: {result['tcp5555_status']}")


def inspect_status(device: Device) -> dict:
    """Return a safe, structured repair snapshot for HA or the CLI."""
    original = device.read(ORIGINAL_PATH)
    original_hash = _sha256(original)
    stock_pids = device.pids(ORIGINAL_PATH)
    patched_pids = device.pids(CANDIDATE_PATH)
    result = {
        "status": "unsupported_firmware",
        "binary_sha256": original_hash,
        "supported_binary": original_hash == EXPECTED_SHA256,
        "stock_pids": stock_pids,
        "patched_pids": patched_pids,
        "patched_process_verified": False,
        "tcp5555_status": "unknown",
    }
    if not result["supported_binary"]:
        return result

    candidate = patch_bytes(original)
    if patched_pids:
        verified = all(
            _sha256(device.read(f"/proc/{pid}/exe")) == _sha256(candidate)
            for pid in patched_pids
        )
        result["patched_process_verified"] = verified
        if not verified:
            result["status"] = "unknown_candidate"
            return result
        result["status"] = "patched_service_unavailable"
        try:
            _check_status(device.host)
        except RuntimeError:
            result["tcp5555_status"] = "unavailable"
        else:
            result["status"] = "patched"
            result["tcp5555_status"] = "healthy"
        return result

    result["status"] = "fix_available"
    try:
        _check_status(device.host)
    except RuntimeError:
        result["tcp5555_status"] = "unavailable"
    else:
        result["tcp5555_status"] = "healthy"
    return result


def apply(
    device: Device, *, confirmed: bool = False, backup_dir: Path | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    """Apply the verified candidate after explicit caller confirmation."""
    def report(phase: str, message: str) -> None:
        if progress:
            try:
                progress(phase, message)
            except Exception:
                pass

    report("checking", "Checking firmware and current adbd process.")
    original, candidate = _preflight(device)
    patched = device.pids(CANDIDATE_PATH)
    if patched:
        if all(_sha256(device.read(f"/proc/{pid}/exe")) == _sha256(candidate) for pid in patched):
            _check_status(device.host)
            print("The supported patch is already running; no change made.")
            return inspect_status(device)
        raise RuntimeError("A different candidate is running; no change made")
    stock_pids = device.pids(ORIGINAL_PATH)
    if len(stock_pids) > 1:
        raise RuntimeError("Multiple stock adbd processes found; no change made")
    report("backing_up", "Saving and verifying local and device backups.")
    backup = _backup_original(original, device.host, backup_dir)
    device.run(f"cp -p {ORIGINAL_PATH} {REMOTE_BACKUP}")
    if _sha256(device.read(REMOTE_BACKUP)) != EXPECTED_SHA256:
        raise RuntimeError("Device backup did not verify; no process was changed")
    report("staging", "Copying and verifying the patched candidate.")
    device.write(CANDIDATE_PATH, candidate)
    print(f"Local original backup: {backup}")
    print(f"Candidate SHA-256: {_sha256(candidate)}")
    if not confirmed:
        print("Candidate staged; running service unchanged. Explicit confirmation is required to switch.")
        return inspect_status(device)
    try:
        report("switching", "Switching the running adbd process.")
        _stop(device, ORIGINAL_PATH)
        _start(device, CANDIDATE_PATH, CANDIDATE_LOG, PID_FILE, report)
    except Exception as failure:
        report("rolling_back", f"Candidate failed ({failure}); restoring stock adbd.")
        try:
            _stop(device, CANDIDATE_PATH)
        finally:
            if not device.pids(ORIGINAL_PATH):
                try:
                    _start(device, ORIGINAL_PATH, STOCK_LOG, "/tmp/lp10-adbd-original-restored.pid", report)
                except Exception as restore_error:
                    raise RuntimeError(
                        f"Candidate failed ({failure}); stock restart also failed ({restore_error})"
                    ) from restore_error
        raise
    print("Patched adbd is running and TCP/5555 returned device status.")
    print("The patch is temporary; a reboot starts the stock binary again.")
    return inspect_status(device)


def rollback(device: Device) -> None:
    _, candidate = _preflight(device)
    patched = device.pids(CANDIDATE_PATH)
    if not patched:
        print("Patched adbd is not running; no change made.")
        return
    if any(_sha256(device.read(f"/proc/{pid}/exe")) != _sha256(candidate) for pid in patched):
        raise RuntimeError("A different candidate is running; no change made")
    confirmation = input("Stop patched adbd and restore stock? Type RESTORE: ")
    if confirmation != "RESTORE":
        print("No change made.")
        return
    _stop(device, CANDIDATE_PATH)
    try:
        _start(device, ORIGINAL_PATH, STOCK_LOG, "/tmp/lp10-adbd-original-restored.pid")
    except Exception as failure:
        try:
            _stop(device, ORIGINAL_PATH)
            _start(device, CANDIDATE_PATH, CANDIDATE_LOG, PID_FILE)
        except Exception as restore_error:
            raise RuntimeError(
                f"Stock rollback failed ({failure}); patched restart also failed ({restore_error})"
            ) from restore_error
        raise RuntimeError(
            f"Stock rollback failed ({failure}); verified patched service was restored"
        ) from failure
    print("Stock adbd is running and TCP/5555 returned device status.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "apply", "rollback"))
    parser.add_argument("host", help="LP10 private IPv4 address")
    parser.add_argument(
        "--prompt-password", action="store_true",
        help="Prompt for a changed SSH password instead of using the bundled factory default",
    )
    args = parser.parse_args()
    try:
        device = Device(args.host, prompt_password=args.prompt_password)
        try:
            if args.action == "apply":
                confirmed = input("Stop stock adbd and start the staged candidate? Type APPLY: ") == "APPLY"
                apply(device, confirmed=confirmed)
            else:
                {"inspect": inspect, "rollback": rollback}[args.action](device)
        finally:
            device.close()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Repair stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
