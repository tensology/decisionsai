"""Set up the Windows launcher PATH, icon, and shortcuts."""
import json
import os
import struct
import subprocess

def png_to_ico(png_path, ico_path):
    """Convert a PNG file to ICO format (single-size icon)."""
    with open(png_path, 'rb') as f:
        png_data = f.read()
    # ICO header: reserved(2) + type=1(2) + count=1(2)
    header = struct.pack('<HHH', 0, 1, 1)
    # Read PNG dimensions from IHDR chunk
    width = struct.unpack('>I', png_data[16:20])[0]
    height = struct.unpack('>I', png_data[20:24])[0]
    w = width if width < 256 else 0
    h = height if height < 256 else 0
    # ICO directory entry: w, h, colors=0, reserved=0, planes=1, bpp=32, size, offset=22
    entry = struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(png_data), 22)
    with open(ico_path, 'wb') as f:
        f.write(header + entry + png_data)

def create_shortcuts(shortcuts):
    """Create missing shortcuts in one PowerShell process."""
    ps_script = '''
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
foreach ($shortcut in (ConvertFrom-Json $env:DECISIONSAI_SHORTCUTS)) {
    $link = $ws.CreateShortcut($shortcut.path)
    $link.TargetPath = $shortcut.target
    $link.Arguments = $shortcut.arguments
    $link.WorkingDirectory = $shortcut.working_dir
    $link.Description = $shortcut.description
    if ($shortcut.icon) { $link.IconLocation = $shortcut.icon }
    $link.Save()
}
'''
    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
        env={**os.environ, 'DECISIONSAI_SHORTCUTS': json.dumps(shortcuts)},
        check=True,
        timeout=90,
    )


def update_user_path(project_root):
    import ctypes
    from ctypes import wintypes
    import winreg

    def normalized(path):
        return os.path.normcase(os.path.normpath(os.path.expandvars(path.strip().strip('"'))))

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0,
                        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as environment:
        try:
            current, registry_type = winreg.QueryValueEx(environment, 'Path')
        except FileNotFoundError:
            current, registry_type = '', winreg.REG_EXPAND_SZ
        root = normalized(project_root)
        entries = current.split(';') if current else []
        matches = [index for index, entry in enumerate(entries) if normalized(entry) == root]
        if len(matches) == 1:
            return
        if matches:
            entries = [entry for index, entry in enumerate(entries) if index not in matches[1:]]
        else:
            entries.append(project_root)
        winreg.SetValueEx(environment, 'Path', 0, registry_type, ';'.join(entries))

    notify = ctypes.windll.user32.SendMessageTimeoutW
    notify.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    # HWND_BROADCAST, WM_SETTINGCHANGE, and SMTO_ABORTIFHUNG.
    notify(0xFFFF, 0x001A, 0, 'Environment', 0x0002, 1000, None)
    print("  Updated the user PATH for DecisionsAI.")


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        update_user_path(project_root)
    except OSError as exc:
        print(f"  Warning: could not update the user PATH: {exc}")
    
    # Convert favicon.png to .ico
    favicon_png = os.path.join(project_root, 'assets', 'icons', 'favicon.png')
    icon_ico = os.path.join(project_root, 'assets', 'icons', 'decisions.ico')
    if os.path.exists(favicon_png) and not os.path.exists(icon_ico):
        try:
            png_to_ico(favicon_png, icon_ico)
            print(f"  Created {icon_ico}")
        except Exception as e:
            print(f"  Warning: could not create .ico: {e}")
            icon_ico = ''
    elif not os.path.exists(icon_ico):
        icon_ico = ''

    # Start Menu folder
    start_menu = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'DecisionsAI')
    os.makedirs(start_menu, exist_ok=True)

    decisions_bat = os.path.join(project_root, 'decisions.bat')
    uninstall_bat = os.path.join(project_root, 'bin', 'uninstall.bat')
    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    shell = os.path.join(os.environ['SystemRoot'], 'explorer.exe')
    shortcuts = [
        (start_menu, 'DecisionsAI', decisions_bat, '', project_root, icon_ico, 'DecisionsAI Voice Assistant'),
        (start_menu, 'DecisionsAI Settings', shell, 'http://127.0.0.1:8765/settings', '', icon_ico, 'DecisionsAI Settings & Web UI'),
        (start_menu, 'Uninstall DecisionsAI', uninstall_bat, '', project_root, '', 'Uninstall DecisionsAI'),
        (desktop, 'DecisionsAI', decisions_bat, '', project_root, icon_ico, 'DecisionsAI Voice Assistant'),
    ]
    pending = []
    for folder, name, target, arguments, working_dir, icon, description in shortcuts:
        path = os.path.join(folder, f'{name}.lnk')
        if not os.path.exists(path):
            pending.append(dict(path=path, target=target, arguments=arguments,
                                working_dir=working_dir, icon=icon, description=description))

    if pending:
        try:
            create_shortcuts(pending)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  Warning: could not create Windows shortcuts: {exc}")
            return 1

    print("  Shortcuts ready")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
