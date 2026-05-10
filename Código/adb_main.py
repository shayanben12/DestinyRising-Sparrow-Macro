import cv2
import numpy as np
import subprocess
import time
import json
import sys
import os

DEBUG_MODE = True
ORIGINAL_RES = [1920, 1080] 
ADB_CMD = ".\\adb_tools\\adb.exe"   
TARGET_DEVICE = ""          

def load_settings():
    global DEBUG_MODE, ORIGINAL_RES
    settings_file = 'settings.json'
    
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                DEBUG_MODE = settings.get('debug_mode', True)
                # This must remain 1920x1080 so the script knows what resolution the JSON coordinates were built for
                ORIGINAL_RES = settings.get('original_resolution', [1920, 1080])
        except json.JSONDecodeError:
            print("[!] Error reading settings.json. Using defaults.")
    else:
        default_settings = {
            "debug_mode": True,
            "original_resolution": [1920, 1080]
        }
        with open(settings_file, 'w') as f:
            json.dump(default_settings, f, indent=4)
            
    print(f"[*] Settings Loaded | Debug: {DEBUG_MODE}")
    
    if DEBUG_MODE:
        os.makedirs('debug', exist_ok=True)

def connect_to_emulator():
    global TARGET_DEVICE
    common_ports = ["16384", "5555", "7555", "16385"]
    
    print("\n[*] Resetting ADB Engine (Clearing ghost connections)...")
    subprocess.run(f"{ADB_CMD} kill-server", shell=True, capture_output=True)
    subprocess.run(f"{ADB_CMD} start-server", shell=True, capture_output=True)
    
    print("[*] Scanning for active emulator ports...")
    
    for port in common_ports:
        device_ip = f"127.0.0.1:{port}"
        sys.stdout.write(f"  -> Testing port {port}... ")
        sys.stdout.flush()
        
        subprocess.run(f"{ADB_CMD} connect {device_ip}", shell=True, capture_output=True)
        
        check = subprocess.run(f"{ADB_CMD} devices", shell=True, capture_output=True, text=True)
        
        if device_ip in check.stdout and "offline" not in check.stdout:
            print(f"[SUCCESS] Locked onto: {device_ip}")
            TARGET_DEVICE = device_ip
            return True 
        else:
            print("[FAILED]")
            
    return False

def capture_screen_adb():
    subprocess.run(f"{ADB_CMD} -s {TARGET_DEVICE} shell screencap -p /sdcard/temp_screen.png", shell=True)
    
    save_path = 'debug/latest_adb_screen.png' if DEBUG_MODE else 'temp_screen.png'
    subprocess.run(f"{ADB_CMD} -s {TARGET_DEVICE} pull /sdcard/temp_screen.png {save_path}", shell=True, capture_output=True)
    
    img_bgr = cv2.imread(save_path)
    
    if img_bgr is None:
        print(f"\n[!] FATAL ERROR: Failed to load the screenshot from the emulator.")
        return None
        
    return img_bgr

def adb_drag_and_hold(start_x, start_y, end_x, end_y, duration_seconds):
    """Sends a hardware swipe from point A to point B over X seconds."""
    duration_ms = int(duration_seconds * 1000)
    subprocess.run(f"{ADB_CMD} -s {TARGET_DEVICE} shell input swipe {int(start_x)} {int(start_y)} {int(end_x)} {int(end_y)} {duration_ms}", shell=True)

def adb_click(x, y):
    """Sends a hardware tap."""
    subprocess.run(f"{ADB_CMD} -s {TARGET_DEVICE} shell input tap {int(x)} {int(y)}", shell=True)

def adb_hold(x, y, duration_seconds):
    """Sends a hardware swipe-in-place (hold)."""
    duration_ms = int(duration_seconds * 1000)
    subprocess.run(f"{ADB_CMD} -s {TARGET_DEVICE} shell input swipe {int(x)} {int(y)} {int(x)} {int(y)} {duration_ms}", shell=True)

def scale_coords(x, y, current_w, current_h):
    """Calculates relative coordinates based on the emulator's actual screen size."""
    scale_x = current_w / ORIGINAL_RES[0]
    scale_y = current_h / ORIGINAL_RES[1]
    return int(x * scale_x), int(y * scale_y)

def find_image_on_screen(screen, template_name, threshold=0.75):
    if screen is None:
        return False, None
        
    # AUTO-DETECT: Get the height of the current emulator window
    current_h, current_w = screen.shape[:2]
    
    # Target the correct templates folder automatically (e.g., templates_480)
    auto_template_dir = f"templates_{current_h}"
    template_path = os.path.join(auto_template_dir, template_name)
        
    if not os.path.exists(template_path):
        print(f"\n[!] FILE NOT FOUND: Cannot find '{template_path}'. Check your folder names and flux.json spelling!")
        return False, None
        
    template = cv2.imread(template_path)
    if template is None:
        print(f"\n[!] CORRUPT IMAGE: '{template_path}' exists but OpenCV cannot read it.")
        return False, None
        
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    loc = np.where(result >= threshold)
    
    if len(loc[0]) > 0:
        h, w = template.shape[:2]
        center_x = loc[1][0] + w // 2
        center_y = loc[0][0] + h // 2
        return True, (center_x, center_y)
        
    return False, None

def run_flux(json_path):
    try:
        with open(json_path, 'r') as f:
            flux = json.load(f)
    except FileNotFoundError:
        print(f"[!] Error: Could not find '{json_path}'.")
        sys.exit(1)
        
    print(f"\n=== Starting Macro: {flux.get('flux_name', 'Unnamed')} ===")
    
    if not connect_to_emulator():
        print("\n[!!!] ERRO CRÍTICO: Não foi possível conectar a nenhuma porta ADB.")
        print("Certifique-se que o MuMu está aberto e o 'ADB Debug' está ligado nas configurações.")
        input("\nPressione Enter para fechar...")
        sys.exit(1)

    cycle_count = 1
    
    try:
        while True: 
            print(f"\n=========================================")
            print(f"          STARTING CYCLE {cycle_count}          ")
            print(f"=========================================")
            
            for step_index, step in enumerate(flux['steps']):
                action = step.get('action')
                skip = step.get('skip', False)
                print(f"\n--- [Step {step_index + 1}/{len(flux['steps'])}] Action: {action.upper()} ---")

                delay_before = step.get('delay_before', 0)
                if delay_before > 0:
                    print(f"\n--- [Step {step_index + 1}/{len(flux['steps'])}] WAITING {delay_before} SECONDS ---")
                    time.sleep(delay_before)
                
                if action == "find_and_click":
                    target_image = step['image']
                    interval = step.get('retry_interval', 1)
                    max_retries = step.get('max_retries', 1)
                    found = False
                    attempts = 0
                    
                    while not found:
                        attempts += 1
                        sys.stdout.write(f"\r  -> Attempt {attempts}/{max_retries} searching for {target_image}... ")
                        sys.stdout.flush()
                        
                        screen = capture_screen_adb()
                        found, coords = find_image_on_screen(screen, target_image)
                        
                        if found:
                            # OpenCV matches the template exactly on screen, no scaling needed here
                            print(f" [SUCCESS] Clicking {coords[0]}, {coords[1]}")
                            adb_click(coords[0], coords[1])
                        else:
                            if skip and attempts >= max_retries:
                                print(f" [SKIP] Moving to next step.")
                                break
                            time.sleep(interval)
                            
                elif action == "find_and_hold_touch":
                    target_image = step['image']
                    t_x = step['touch_x']
                    t_y = step['touch_y']
                    hold_time = step.get('hold_duration', 1)
                    interval = step.get('retry_interval', 1)
                    max_retries = step.get('max_retries', 1)
                    found = False
                    attempts = 0
                    
                    while not found:
                        attempts += 1
                        sys.stdout.write(f"\r  -> Attempt {attempts}/{max_retries} searching for {target_image}... ")
                        sys.stdout.flush()
                        
                        screen = capture_screen_adb()
                        found, _ = find_image_on_screen(screen, target_image)
                        
                        if found:
                            # Scale the hardcoded JSON coordinates to fit the current screen
                            current_h, current_w = screen.shape[:2]
                            scaled_x, scaled_y = scale_coords(t_x, t_y, current_w, current_h)
                            print(f" [SUCCESS] Holding touch at {scaled_x}, {scaled_y} for {hold_time}s")
                            adb_hold(scaled_x, scaled_y, hold_time)
                        else:
                            if skip and attempts >= max_retries:
                                print(f" [SKIP] Moving to next step.")
                                break
                            time.sleep(interval)

                elif action == "joystick_drag":
                    target_image = step['image']
                    s_x = step['start_x']
                    s_y = step['start_y']
                    e_x = step['end_x']
                    e_y = step['end_y']
                    hold_time = step.get('hold_duration', 1)
                    interval = step.get('retry_interval', 1)
                    max_retries = step.get('max_retries', 1)
                    found = False
                    attempts = 0
                    
                    while not found:
                        attempts += 1
                        sys.stdout.write(f"\r  -> Attempt {attempts}/{max_retries} searching for {target_image}... ")
                        sys.stdout.flush()
                        
                        screen = capture_screen_adb()
                        found, _ = find_image_on_screen(screen, target_image)
                        
                        if found:
                            # Scale the hardcoded JSON coordinates to fit the current screen
                            current_h, current_w = screen.shape[:2]
                            scaled_sx, scaled_sy = scale_coords(s_x, s_y, current_w, current_h)
                            scaled_ex, scaled_ey = scale_coords(e_x, e_y, current_w, current_h)
                            print(f" [SUCCESS] Dragging joystick from ({scaled_sx},{scaled_sy}) to ({scaled_ex},{scaled_ey}) for {hold_time}s")
                            adb_drag_and_hold(scaled_sx, scaled_sy, scaled_ex, scaled_ey, hold_time)
                        else:
                            if skip and attempts >= max_retries:
                                print(f" [SKIP] Moving to next step.")
                                break
                            time.sleep(interval)

                elif action == "wait_for_image":
                    target_image = step['image']
                    timeout = step.get('timeout', 60)
                    start_wait = time.time()
                    found = False
                    
                    while not found and (time.time() - start_wait) < timeout:
                        elapsed = int(time.time() - start_wait)
                        sys.stdout.write(f"\r  -> Waiting {elapsed}s/{timeout}s for {target_image}... ")
                        sys.stdout.flush()
                        
                        screen = capture_screen_adb()
                        found, _ = find_image_on_screen(screen, target_image)
                        if not found:
                            time.sleep(1)
                            
                    if found:
                        print(f" [SUCCESS] Appeared!")
                    else:
                        print(f" [TIMEOUT] Did not appear.")
                        
                elif action == "stop_script":
                    target_image = step['image']
                    sys.stdout.write(f"\r  -> Safety check for {target_image}... ")
                    sys.stdout.flush()
                    screen = capture_screen_adb()
                    found, _ = find_image_on_screen(screen, target_image)
                    if found:
                        print(f"\n[!!!] FATAL: Found {target_image}. Stopping.")
                        sys.exit(0)
                    else:
                        print(" [CLEAR]")
            
            print(f"\n=== Cycle {cycle_count} Complete! ===")
            cycle_count += 1
            
    except KeyboardInterrupt:
        print("\n\n[!] Script stopped manually by user (Ctrl+C).")
        sys.exit(0)

if __name__ == "__main__":
    load_settings() 
    time.sleep(1)
    run_flux('flux.json')