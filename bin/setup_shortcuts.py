"""Create Windows Start Menu shortcuts and convert favicon to .ico."""
import os
import struct

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

def create_shortcut(shortcut_path, target, arguments='', working_dir='', icon_path='', description=''):
    """Create a Windows .lnk shortcut using PowerShell."""
    # Escape single quotes for PowerShell
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{shortcut_path}')
$s.TargetPath = '{target}'
$s.Arguments = '{arguments}'
$s.WorkingDirectory = '{working_dir}'
$s.Description = '{description}'
'''
    if icon_path:
        ps_script += f"$s.IconLocation = '{icon_path}'\n"
    ps_script += "$s.Save()\n"
    
    import subprocess
    subprocess.run(['powershell', '-NoProfile', '-Command', ps_script],
                   capture_output=True, timeout=10)

def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
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

    venv_dir = os.path.join(os.path.expanduser('~'), '.virtualenvs', 'decisions')
    pythonw = os.path.join(venv_dir, 'Scripts', 'pythonw.exe')
    decisions_bat = os.path.join(project_root, 'decisions.bat')
    uninstall_bat = os.path.join(project_root, 'bin', 'uninstall.bat')

    # 1. DecisionsAI shortcut (launches the app)
    app_shortcut = os.path.join(start_menu, 'DecisionsAI.lnk')
    if not os.path.exists(app_shortcut):
        create_shortcut(
            app_shortcut,
            target=decisions_bat,
            working_dir=project_root,
            icon_path=icon_ico,
            description='DecisionsAI Voice Assistant'
        )
        print(f"  Created shortcut: DecisionsAI")

    # 2. DecisionsAI Settings (opens web UI)
    settings_shortcut = os.path.join(start_menu, 'DecisionsAI Settings.lnk')
    if not os.path.exists(settings_shortcut):
        create_shortcut(
            settings_shortcut,
            target='http://127.0.0.1:8765/settings',
            icon_path=icon_ico,
            description='DecisionsAI Settings & Web UI'
        )
        print(f"  Created shortcut: DecisionsAI Settings")

    # 3. Uninstall DecisionsAI
    uninstall_shortcut = os.path.join(start_menu, 'Uninstall DecisionsAI.lnk')
    if not os.path.exists(uninstall_shortcut):
        create_shortcut(
            uninstall_shortcut,
            target=uninstall_bat,
            working_dir=project_root,
            description='Uninstall DecisionsAI'
        )
        print(f"  Created shortcut: Uninstall DecisionsAI")

    # 4. Desktop shortcut
    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    desktop_shortcut = os.path.join(desktop, 'DecisionsAI.lnk')
    if not os.path.exists(desktop_shortcut):
        create_shortcut(
            desktop_shortcut,
            target=decisions_bat,
            working_dir=project_root,
            icon_path=icon_ico,
            description='DecisionsAI Voice Assistant'
        )
        print(f"  Created desktop shortcut")

    print("  Shortcuts ready")

if __name__ == '__main__':
    main()
