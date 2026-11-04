import ctypes
from ctypes import wintypes
import json
import sys
import time
import clr

clr.AddReference("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/WPF/UIAutomationClient.dll")
clr.AddReference("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/WPF/UIAutomationTypes.dll")
from System import IntPtr
from System.Windows.Automation import AutomationElement, ValuePattern

user32 = ctypes.WinDLL("user32", use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


def windows(parent=None, all_processes=False):
    result = []

    def collect(handle, _):
        process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process))
        owner_process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetAncestor(handle, 3), ctypes.byref(owner_process))
        if (all_processes or int(sys.argv[1]) in (process.value, owner_process.value)) and user32.IsWindowVisible(handle):
            title, kind = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            user32.GetClassNameW(handle, kind, len(kind))
            result.append((handle, title.value, kind.value))
        return True

    callback = callback_type(collect)
    if parent is None:
        user32.EnumWindows(callback, 0)
    else:
        user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        user32.EnumChildWindows(parent, callback, 0)
    return result


action = sys.argv[2]
deadline = time.monotonic() + 15
while True:
    found = windows()
    candidates = [item for item in found if item[2] == "#32770"]
    if action == "close":
        candidates = [item for item in found if item[1] == "SIMUWELD SD" and item[2] != "#32770"]
    if candidates or action == "list":
        break
    if time.monotonic() >= deadline:
        raise RuntimeError(f"未找到窗口：{found}; dialogs={[item for item in windows(all_processes=True) if item[2] == '#32770']}")
    time.sleep(0.1)

if action == "list":
    print(json.dumps([(item, windows(item[0])) for item in found], ensure_ascii=False))
elif action == "close":
    user32.PostMessageW(candidates[0][0], 0x0010, 0, 0)
elif action in ("accept", "cancel", "file"):
    dialog = candidates[0][0]
    if action == "file":
        while True:
            children = windows(dialog)
            edits = [item for item in children if item[2] == "Edit"]
            if edits or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if len(edits) != 1:
            raise RuntimeError(f"文件名输入框数量异常：{children}")
        element = AutomationElement.FromHandle(IntPtr(edits[0][0]))
        element.GetCurrentPattern(ValuePattern.Pattern).SetValue(sys.argv[3])
    button = user32.GetDlgItem(dialog, 2 if action == "cancel" else 1)
    if not button:
        raise RuntimeError("未找到对话框操作按钮")
    if not user32.PostMessageW(dialog, 0x0111, 2 if action == "cancel" else 1, button):
        raise ctypes.WinError(ctypes.get_last_error())
else:
    raise ValueError(action)
