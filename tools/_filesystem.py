"""Handle-based local filesystem backend; no resolve-then-open execution."""
import errno
import os
import stat
from contextlib import contextmanager, ExitStack
from pathlib import Path

from agentfence.contracts import FenceError


def _unsafe():
    raise FenceError("UNSAFE_TARGET", "Links, special files and replaced paths are blocked.")


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _unsafe()


if os.name == "nt":
    import ctypes as C
    from ctypes import wintypes as W
    import msvcrt

    _kernel = C.WinDLL("kernel32", use_last_error=True)

    class _Info(C.Structure):
        _fields_ = [("attributes", W.DWORD), ("created", W.FILETIME),
                    ("accessed", W.FILETIME), ("written", W.FILETIME),
                    ("volume", W.DWORD), ("size_high", W.DWORD), ("size_low", W.DWORD),
                    ("links", W.DWORD), ("index_high", W.DWORD), ("index_low", W.DWORD)]

    _kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID,
                                    W.DWORD, W.DWORD, W.HANDLE]
    _kernel.CreateFileW.restype = W.HANDLE
    _kernel.CloseHandle.argtypes = [W.HANDLE]
    _kernel.CloseHandle.restype = W.BOOL
    _kernel.GetFileInformationByHandle.argtypes = [W.HANDLE, C.POINTER(_Info)]
    _kernel.GetFileInformationByHandle.restype = W.BOOL
    _kernel.GetFileType.argtypes = [W.HANDLE]
    _kernel.GetFileType.restype = W.DWORD
    _kernel.SetFileInformationByHandle.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]
    _kernel.SetFileInformationByHandle.restype = W.BOOL
    _INVALID = C.c_void_p(-1).value

    class _UnicodeString(C.Structure):
        _fields_ = [("length", W.USHORT), ("maximum", W.USHORT), ("buffer", W.LPWSTR)]

    class _ObjectAttributes(C.Structure):
        _fields_ = [("length", W.ULONG), ("root", W.HANDLE), ("name", C.POINTER(_UnicodeString)),
                    ("attributes", W.ULONG), ("security", W.LPVOID), ("qos", W.LPVOID)]

    class _StatusUnion(C.Union):
        _fields_ = [("status", W.LONG), ("pointer", W.LPVOID)]

    class _StatusBlock(C.Structure):
        _fields_ = [("status", _StatusUnion), ("information", C.c_size_t)]

    _nt = C.WinDLL("ntdll")
    _nt.NtCreateFile.argtypes = [C.POINTER(W.HANDLE), W.DWORD, C.POINTER(_ObjectAttributes),
                               C.POINTER(_StatusBlock), W.LPVOID, W.ULONG, W.ULONG,
                               W.ULONG, W.ULONG, W.LPVOID, W.ULONG]
    _nt.NtCreateFile.restype = W.LONG
    _nt.RtlNtStatusToDosError.argtypes = [W.LONG]
    _nt.RtlNtStatusToDosError.restype = W.ULONG

    def _win_open(path, *, parent=None, directory=False, mode="r"):
        access = 0x80 if directory is not False else 0x80000000  # attributes / GENERIC_READ
        if not directory and mode in {"w", "x"}:
            access |= 0x40000000  # GENERIC_WRITE
        if mode == "d":
            access |= 0x10000  # DELETE
        sharing = 3 if directory is not False else 1
        if parent is None:
            # Only the trusted local drive root is opened by absolute path.
            handle = _kernel.CreateFileW(str(path), access, sharing, None,
                                         1 if mode == "x" else 3, 0x02200000, None)
            error = C.get_last_error() if handle == _INVALID else 0
        else:
            name_buffer = C.create_unicode_buffer(str(path))
            name = _UnicodeString(len(str(path).encode("utf-16-le")),
                                  C.sizeof(name_buffer), C.cast(name_buffer, W.LPWSTR))
            attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent, C.pointer(name),
                                           0x1040, None, None)  # CASE_INSENSITIVE | DONT_REPARSE
            status_block = _StatusBlock()
            out_handle = W.HANDLE()
            status = _nt.NtCreateFile(C.byref(out_handle), access | 0x100000,
                                      C.byref(attributes), C.byref(status_block), None, 0,
                                      sharing, 2 if mode == "x" else 1,
                                      0x200020, None, 0)  # OPEN_REPARSE_POINT | SYNC
            if (status & 0xFFFFFFFF) in {0xC000050B, 0x8000002D, 0xC0000279}:
                _unsafe()
            error = _nt.RtlNtStatusToDosError(status) if status < 0 else 0
            handle = out_handle.value
        if error:
            if error in {2, 3}:
                raise FileNotFoundError("Target missing")
            if error in {80, 183}:
                raise FileExistsError("Target exists")
            raise C.WinError(error)
        try:
            info = _Info()
            if not _kernel.GetFileInformationByHandle(handle, C.byref(info)):
                raise C.WinError(C.get_last_error())
            if (_kernel.GetFileType(handle) != 1 or info.attributes & 0x400
                    or (directory is not None and bool(info.attributes & 0x10) != directory)
                    or (not info.attributes & 0x10 and info.links != 1)):
                _unsafe()
            identity = (info.volume, info.index_high, info.index_low)
            return handle, identity
        except BaseException:
            _kernel.CloseHandle(handle)
            raise


class _Directory:
    def __init__(self, path, descriptor):
        self.path, self.descriptor = path, descriptor

    @contextmanager
    def file(self, name, mode):
        if os.name == "nt":
            handle, _ = _win_open(name, parent=self.descriptor, mode=mode)
            flags = os.O_BINARY | (os.O_RDWR if mode in {"w", "x"} else os.O_RDONLY)
            try:
                descriptor = msvcrt.open_osfhandle(handle, flags)
            except BaseException:
                _kernel.CloseHandle(handle)
                raise
        else:
            flags = os.O_NOFOLLOW | os.O_NONBLOCK
            flags |= os.O_RDWR if mode in {"w", "x"} else os.O_RDONLY
            if mode == "x":
                flags |= os.O_CREAT | os.O_EXCL
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=self.descriptor)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EISDIR}:
                    _unsafe()
                raise
        try:
            _regular(os.fstat(descriptor))
            yield descriptor
        finally:
            os.close(descriptor)

    def delete(self, name):
        with self.file(name, "d") as descriptor:
            if os.name == "nt":
                # Delete the validated handle, not a re-resolved path.
                disposition = W.BOOL(True)
                if not _kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(descriptor), 4,
                                                         C.byref(disposition), C.sizeof(disposition)):
                    raise C.WinError(C.get_last_error())
            else:
                # unlink never follows a swapped leaf symlink. Parent remains pinned.
                info = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino):
                    _unsafe()
                os.unlink(name, dir_fd=self.descriptor)

    def kind(self, name):
        # Listing does not open file data. Never reveal link/special/hardlink entries.
        if os.name == "nt":
            try:
                handle, _ = _win_open(name, parent=self.descriptor, directory=None)
            except FenceError:
                return None
            try:
                info = _Info()
                if not _kernel.GetFileInformationByHandle(handle, C.byref(info)):
                    raise C.WinError(C.get_last_error())
                return "directory" if info.attributes & 0x10 else "file"
            finally:
                _kernel.CloseHandle(handle)
        else:
            info = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            return "directory"
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            return "file"
        return None


class Workspace:
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        self.identity = None
        if os.name not in {"nt", "posix"}:
            raise FenceError("UNSUPPORTED_PLATFORM", "A local Windows or POSIX filesystem is required.")
        if os.name == "posix" and not (hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd):
            raise FenceError("UNSUPPORTED_PLATFORM", "Safe descriptor-relative access is unavailable.")
        if os.name == "nt" and (str(self.root).startswith("\\\\") or not self.root.drive):
            raise FenceError("CONFIG_ERROR", "Use a local drive workspace, not a network or device path.")
        with self.directory(".") as directory:
            self.identity = directory.identity

    @contextmanager
    def directory(self, relative):
        with ExitStack() as stack:
            if os.name == "nt":
                path = Path(self.root.anchor)
                handle, identity = _win_open(path, directory=True)
                stack.callback(_kernel.CloseHandle, handle)
                for part in self.root.parts[1:]:
                    path = path / part
                    handle, identity = _win_open(part, parent=handle, directory=True)
                    stack.callback(_kernel.CloseHandle, handle)
                if self.identity is not None and identity != self.identity:
                    _unsafe()
                for part in (() if relative == "." else relative.split("/")):
                    path = path / part
                    handle, _ = _win_open(part, parent=handle, directory=True)
                    stack.callback(_kernel.CloseHandle, handle)
                directory = _Directory(path, handle)
            else:
                descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, descriptor)
                for part in self.root.parts[1:]:
                    descriptor = self._open_child_dir(descriptor, part)
                    stack.callback(os.close, descriptor)
                info = os.fstat(descriptor)
                identity = (info.st_dev, info.st_ino)
                if self.identity is not None and identity != self.identity:
                    _unsafe()
                for part in (() if relative == "." else relative.split("/")):
                    descriptor = self._open_child_dir(descriptor, part)
                    stack.callback(os.close, descriptor)
                directory = _Directory(self.root / relative, descriptor)
            directory.identity = identity
            yield directory

    @staticmethod
    def _open_child_dir(parent, name):
        try:
            return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                _unsafe()
            raise
