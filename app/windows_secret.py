"""Protect local credentials with Windows DPAPI for the current user."""
import base64
import ctypes
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _transform(data, operation):
    if not hasattr(ctypes, "WinDLL"):
        raise ValueError("APIキーの保存にはWindowsが必要です。")
    crypto = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    transform = getattr(crypto, operation)
    transform.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    transform.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.addressof(buffer))
    result = DataBlob()
    if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "WindowsでAPIキーを保護または読み取りできませんでした。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def protect(secret):
    return base64.b64encode(_transform(secret.encode("utf-8"), "CryptProtectData")).decode("ascii")


def unprotect(value):
    return _transform(base64.b64decode(value, validate=True), "CryptUnprotectData").decode("utf-8")
