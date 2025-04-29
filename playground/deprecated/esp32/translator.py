import subprocess
import time
import evdev
from evdev import InputDevice, categorize, ecodes

def run_command(command, timeout=30):
    """Run a command and return its output"""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        print(f"Command '{' '.join(command)}' output:\n{result.stdout}")
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"Command '{' '.join(command)}' timed out")
    except Exception as e:
        print(f"Error running command '{' '.join(command)}': {e}")
    return ""

def get_bluetooth_devices():
    print("\nGetting device list...")
    devices_output = run_command(['bluetoothctl', 'devices'])
    
    devices = []
    for line in devices_output.splitlines():
        if line.strip().startswith('Device'):
            parts = line.strip().split(' ', 2)
            if len(parts) >= 3:
                device = {
                    'mac': parts[1],
                    'name': parts[2],
                    'full_info': line.strip()
                }
                devices.append(device)
                print(f"Found device: {device['name']} ({device['mac']})")
    
    if devices:
        print("\nDiscovered devices:")
        for i, device in enumerate(devices, 1):
            print(f"{i}. {device['name']} ({device['mac']})")
        
        while True:
            try:
                choice = int(input("\nEnter device number (or 0 to exit): "))
                if choice == 0:
                    return None
                if 1 <= choice <= len(devices):
                    return devices[choice-1]
                print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a valid number.")
    else:
        print("No devices found.")
        return None

def connect_to_device(mac_address):
    print(f"\nAttempting to connect to {mac_address}...")
    
    # Check if the device is already connected
    info_output = run_command(['bluetoothctl', 'info', mac_address])
    if "Connected: yes" in info_output:
        print("Device is already connected.")
        return True
    
    # If not connected, try to connect
    connect_output = run_command(['bluetoothctl', 'connect', mac_address])
    
    if "Connection successful" in connect_output:
        print("Successfully connected!")
        return True
    else:
        print("Failed to connect. Attempting to pair...")
        pair_output = run_command(['bluetoothctl', 'pair', mac_address])
        if "Pairing successful" in pair_output:
            print("Pairing successful. Attempting to connect again...")
            connect_output = run_command(['bluetoothctl', 'connect', mac_address])
            if "Connection successful" in connect_output:
                print("Successfully connected!")
                return True
    
    print("Failed to connect.")
    return False

def find_bluetooth_device(device_name):
    print(f"Searching for input device: {device_name}")
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for device in devices:
        print(f"Found device: {device.name}")
        if device_name.lower() in device.name.lower():
            return device
    return None

def listen_for_inputs(device):
    print(f"Listening for inputs from {device.name}...")
    print("Press Ctrl+C to stop listening.")
    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                key_event = categorize(event)
                if key_event.keystate == key_event.key_down:
                    print(f"Key pressed: {key_event.keycode}")
            elif event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    print(f"X axis: {event.value}")
                elif event.code == ecodes.ABS_Y:
                    print(f"Y axis: {event.value}")
    except KeyboardInterrupt:
        print("\nStopped listening for inputs.")

def main():
    print("Starting Bluetooth initialization...")
    
    service_status = run_command(['systemctl', 'is-active', 'bluetooth'])
    if "inactive" in service_status:
        print("Bluetooth service is not running. Attempting to start...")
        run_command(['sudo', 'systemctl', 'start', 'bluetooth'])
        time.sleep(2)
    
    print("Getting Bluetooth devices...")
    selected_device = get_bluetooth_devices()
    
    if selected_device:
        print("\nSelected device details:")
        print(f"MAC Address: {selected_device['mac']}")
        print(f"Name: {selected_device['name']}")
        print(f"Full Info: {selected_device['full_info']}")
        
        max_attempts = 3
        for attempt in range(max_attempts):
            if connect_to_device(selected_device['mac']):
                print("Waiting for device to initialize...")
                time.sleep(5)  # Give more time for the device to be recognized
                
                input_device = find_bluetooth_device(selected_device['name'])
                if input_device:
                    listen_for_inputs(input_device)
                    break
                else:
                    print(f"Could not find input device for {selected_device['name']}")
                    print("Available devices:")
                    run_command(['ls', '-l', '/dev/input/event*'])
                    print("You might need to run this script with sudo.")
            else:
                if attempt < max_attempts - 1:
                    print(f"Connection attempt {attempt + 1} failed. Retrying...")
                    time.sleep(2)
                else:
                    print("Failed to connect to the device after multiple attempts.")
                    
        # If we couldn't connect or find the input device, print debug info
        if not input_device:
            print("\nDebug Information:")
            print("Bluetooth Controller Status:")
            run_command(['bluetoothctl', 'show'])
            print("\nAll Known Devices:")
            run_command(['bluetoothctl', 'devices'])
            print("\nSelected Device Info:")
            run_command(['bluetoothctl', 'info', selected_device['mac']])

if __name__ == "__main__":
    main()