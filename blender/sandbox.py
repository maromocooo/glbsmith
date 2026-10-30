"""Kernel sandbox for the Blender worker, using Landlock (no root needed).

The agent runs arbitrary Python inside Blender, and what it writes can be
steered by text inside a product photo, so the worker locks itself down before
it runs any of that code:

- files: read-only access to Blender and the system libraries, read-write
  access to the job directory, nothing else (no home directory, no credentials)
- network: no TCP connect or bind (Landlock ABI 4+)
- no signals or abstract unix sockets to processes outside (Landlock ABI 6+)

Landlock does not cover UDP. An audit hook refuses to create internet sockets
and to start processes from Python, which catches ordinary code but not a
determined attacker using ctypes.
"""

import ctypes
import os
import socket
import sys

_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
_CREATE_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_EXECUTE, _WRITE, _READ, _READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_FILE_ONLY = _EXECUTE | _WRITE | _READ | (1 << 14) | (1 << 15)  # + truncate, ioctl on devices


def _fs_rights(abi: int) -> int:
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= 1 << 13  # refer (renames across directories)
    if abi >= 3:
        rights |= 1 << 14  # truncate
    if abi >= 5:
        rights |= 1 << 15  # ioctl on devices
    return rights


class SandboxUnavailable(RuntimeError):
    pass


def landlock_abi() -> int:
    """Landlock ABI version, or 0 if the kernel does not offer it."""
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(_SYS_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_VERSION))
    return max(abi, 0)


def restrict(*, read: list[str], write: list[str], write_files: list[str] = ()) -> int:
    """Apply the sandbox to this process and its children; returns the ABI used."""
    libc = ctypes.CDLL(None, use_errno=True)
    abi = landlock_abi()
    if abi < 1:
        raise SandboxUnavailable("This kernel does not offer Landlock.")
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxUnavailable("Could not set no_new_privs.")

    all_fs = _fs_rights(abi)

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("fd", ctypes.c_int32)]

    attr = RulesetAttr(all_fs, 0b11 if abi >= 4 else 0, 0b11 if abi >= 6 else 0)
    size = 24 if abi >= 6 else 16 if abi >= 4 else 8
    ruleset = libc.syscall(_SYS_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset < 0:
        raise SandboxUnavailable(f"landlock_create_ruleset failed: {os.strerror(ctypes.get_errno())}")

    def allow(path: str, rights: int) -> None:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return  # not on this system
        if not os.path.isdir(path):
            rights &= _FILE_ONLY
        rule = PathBeneath(rights & all_fs, fd)
        done = libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH),
                            ctypes.byref(rule), ctypes.c_uint32(0))
        error = ctypes.get_errno()
        os.close(fd)
        if done != 0:
            raise SandboxUnavailable(f"landlock_add_rule failed for {path}: {os.strerror(error)}")

    for path in read:
        allow(path, _EXECUTE | _READ | _READ_DIR)
    for path in write:
        allow(path, all_fs)
    for path in write_files:
        allow(path, _READ | _WRITE)

    if libc.syscall(_SYS_RESTRICT, ctypes.c_int(ruleset), ctypes.c_uint32(0)) != 0:
        raise SandboxUnavailable(f"landlock_restrict_self failed: {os.strerror(ctypes.get_errno())}")
    os.close(ruleset)
    return abi


def block_python_escapes() -> None:
    """Refuse internet sockets (including UDP) and new processes from Python."""
    def hook(event, args):
        if event == "socket.__new__" and len(args) > 1 and args[1] in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("Network access is not available in this sandbox.")
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"):
            raise PermissionError("Starting processes is not available in this sandbox.")

    sys.addaudithook(hook)
