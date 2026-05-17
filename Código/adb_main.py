# =======================================================
# Made by: Zeca[Lobo] - LATAM Server
# =======================================================



import cv2
import numpy as np
import subprocess
import time
import json
import sys
import os
import threading
import ctypes

# Path to the ADB executable. Ensure this folder exists in the same directory as the script.
ADB_CMD = ".\\adb_tools\\adb.exe"

# Global variables used to share data between the worker threads and the UI thread
emulator_statuses = {} # Stores the current text status of each emulator by its auto-detected name
emulator_cycles = {}   # Stores the current cycle count of each emulator by its auto-detected name
global_cycles_lock = threading.Lock() # Prevents multiple threads from overwriting the save file at the exact same time

# =======================================================
# AUTO-DETECTION: GET EMULATOR NAME AUTOMATICALLY
# =======================================================
def get_emulator_name_from_port(port):
    """
    Magic function that finds the actual Windows Window Title (e.g., 'Account 1')
    using the ADB port. If it fails, it calculates the MuMu Instance number mathematically.
    """
    port_num = int(port)
    
    # Mathematical fallback based on MuMu's strict port assignment rules
    # Inst 1 = 16384, Inst 2 = 16416 (+32), Inst 3 = 16448 (+32)...
    calculated_instance = int(((port_num - 16384) / 32) + 1)
    fallback_name = f"Instance {calculated_instance}" if port_num >= 16384 else f"Port {port}"
    
    if os.name != 'nt': 
        return fallback_name
        
    try:
        # 1. Ask Windows which Process ID (PID) is listening on this ADB port
        netstat = subprocess.run(f"netstat -ano | findstr LISTENING | findstr :{port}", shell=True, capture_output=True, text=True)
        if not netstat.stdout:
            return fallback_name
            
        first_line = netstat.stdout.strip().split('\n')[0]
        pid = int(first_line.split()[-1])
        
        # 2. Ask Windows API for the Window Title of that specific PID
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        titles = []
        def foreach_window(hwnd, lParam):
            if IsWindowVisible(hwnd):
                window_pid = ctypes.c_uint(0)
                GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                if window_pid.value == pid:
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        titles.append(buff.value)
            return True
        
        EnumWindows(EnumWindowsProc(foreach_window), 0)
        
        if titles:
            # MuMu usually titles windows like "Account XXXX - MuMu Player 12"
            # This splits the string and keeps only your custom name
            raw_title = titles[0]
            clean_title = raw_title.split(" - MuMu")[0]
            return clean_title
            
    except Exception:
        pass
        
    return fallback_name

# =======================================================
# MEMORY SYSTEM (Save/Load Cycles by Custom Name)
# =======================================================
def load_saved_cycles():
    """Loads the cycle counts from previous sessions so the bot resumes correctly."""
    if os.path.exists('cycles.json'):
        try:
            with open('cycles.json', 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}

def save_cycle_count(name, count):
    """Safely saves the cycle count to a JSON file using the auto-detected instance name."""
    with global_cycles_lock:
        cycles = load_saved_cycles()
        cycles[name] = count
        with open('cycles.json', 'w') as f:
            json.dump(cycles, f, indent=4)

# =======================================================
# BASIC SETTINGS & CONNECTION
# =======================================================
def load_settings():
    """Loads or creates the settings.json file containing base configurations."""
    settings_file = 'settings.json'
    
    settings_data = {
        "debug_mode": True,
        "original_resolution": [1920, 1080] # The resolution the flux coordinates were originally mapped on
    }
    
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                settings_data["debug_mode"] = settings.get('debug_mode', True)
                settings_data["original_resolution"] = settings.get('original_resolution', [1920, 1080])
        except json.JSONDecodeError:
            pass
    else:
        # Create a default file if it doesn't exist
        with open(settings_file, 'w') as f:
            json.dump(settings_data, f, indent=4)
            
    # Create the debug folder to store screenshots if debug mode is active
    if settings_data["debug_mode"]:
        os.makedirs('debug', exist_ok=True)
        
    return settings_data

def get_connected_devices():
    """
    Kills any ghost ADB servers, starts a fresh one, and scans default emulator ports.
    Returns a list of IPs for emulators that are actually alive and responding.
    """
    common_ports = ["16384", "16416", "16448", "16480", "16512", "5555", "7555"]
    connected_devices = []
    seen_boot_ids = set() # Used to prevent connecting to the same emulator twice via different ports
    
    subprocess.run(f"{ADB_CMD} kill-server", shell=True, capture_output=True)
    subprocess.run(f"{ADB_CMD} start-server", shell=True, capture_output=True)
    
    # Send a connect request to all common ports
    for port in common_ports:
        device_ip = f"127.0.0.1:{port}"
        subprocess.run(f"{ADB_CMD} connect {device_ip}", shell=True, capture_output=True)
        
    # Check which ones actually connected
    check = subprocess.run(f"{ADB_CMD} devices", shell=True, capture_output=True, text=True)
    
    for line in check.stdout.splitlines():
        if "127.0.0.1" in line and "device" in line and "offline" not in line:
            ip = line.split()[0]
            try:
                # Send a test ping to ensure the Android system is fully booted and responsive
                test = subprocess.run(f"{ADB_CMD} -s {ip} shell echo alive", shell=True, capture_output=True, text=True, timeout=3)
                if "alive" in test.stdout:
                    # Get a unique ID from the emulator to avoid duplicate instances
                    boot_id_req = subprocess.run(f"{ADB_CMD} -s {ip} shell cat /proc/sys/kernel/random/boot_id", shell=True, capture_output=True, text=True)
                    boot_id = boot_id_req.stdout.strip()
                    
                    if boot_id and boot_id not in seen_boot_ids:
                        seen_boot_ids.add(boot_id)
                        connected_devices.append(ip)
                    else:
                        subprocess.run(f"{ADB_CMD} disconnect {ip}", shell=True, capture_output=True)
                else:
                    subprocess.run(f"{ADB_CMD} disconnect {ip}", shell=True, capture_output=True)
            except subprocess.TimeoutExpired:
                # Disconnect if the emulator is frozen or taking too long
                subprocess.run(f"{ADB_CMD} disconnect {ip}", shell=True, capture_output=True)
                
    return connected_devices

def ui_loop():
    """
    This function runs in its own thread. It constantly clears the terminal
    and updates the status of all active emulators using their Auto-Detected Names.
    """
    if os.name == 'nt': os.system('') 
    os.system('cls' if os.name == 'nt' else 'clear')
    
    while True:
        # Move cursor to top-left instead of printing new lines (prevents screen flickering)
        sys.stdout.write('\033[H') 
        sys.stdout.write("=== Multi-Instance Bot Status ===\033[K\n")
        sys.stdout.write("Press Ctrl+C to shut down all bots safely.\033[K\n\n")
        
        # Print the cycle count and current action for each emulator using its Name
        for name in list(emulator_statuses.keys()):
            status = emulator_statuses.get(name, "")
            cycle = emulator_cycles.get(name, 1)
            sys.stdout.write(f"[{name}] Cycle {cycle} | {status}\033[K\n")
            
        sys.stdout.flush()
        time.sleep(0.1)

# =======================================================
# BOT INSTANCE CLASS (One object per Emulator)
# =======================================================
class MacroBot:
    def __init__(self, device_ip, settings, flux_data, auto_name):
        self.target_device = device_ip
        self.port = device_ip.split(':')[-1] # Extract just the port number for cleaner file paths
        self.name = auto_name # Automatically grabbed from Windows Title
        
        self.debug_mode = settings["debug_mode"]
        self.original_res = settings["original_resolution"]
        self.flux = flux_data
        self.is_running = True
        
        # Load memory for this specific emulator using its Name
        self.cycle_count = load_saved_cycles().get(self.name, 1)
        emulator_cycles[self.name] = self.cycle_count

    def log(self, message):
        """Updates the global dictionary so the UI thread can display the message under the Auto Name."""
        emulator_statuses[self.name] = message

    def capture_screen_adb(self):
        """Takes a screenshot via ADB. We still use self.port here because names with spaces break ADB file paths."""
        sdcard_path = f"/sdcard/temp_screen_{self.port}.png"
        local_path = f"debug/latest_adb_screen_{self.port}.png" if self.debug_mode else f"temp_screen_{self.port}.png"
        
        # Command 1: Capture on Android device
        subprocess.run(f"{ADB_CMD} -s {self.target_device} shell screencap -p {sdcard_path}", shell=True, capture_output=True)
        # Command 2: Pull the image to the PC
        subprocess.run(f"{ADB_CMD} -s {self.target_device} pull {sdcard_path} {local_path}", shell=True, capture_output=True)
        
        # Read the image using OpenCV
        img_bgr = cv2.imread(local_path)
        if img_bgr is None:
            self.log("[!] Connection lost. Shutting down this instance's thread.")
            self.is_running = False
            return None
        return img_bgr

    def adb_drag_and_hold(self, start_x, start_y, end_x, end_y, duration_seconds):
        """Simulates dragging a finger from Point A to Point B."""
        duration_ms = int(duration_seconds * 1000)
        subprocess.run(f"{ADB_CMD} -s {self.target_device} shell input swipe {int(start_x)} {int(start_y)} {int(end_x)} {int(end_y)} {duration_ms}", shell=True)
        # FORCE the thread to wait for the physical drag to finish before moving to the next step
        time.sleep(duration_seconds)

    def adb_click(self, x, y):
        """Simulates a quick tap on the screen."""
        subprocess.run(f"{ADB_CMD} -s {self.target_device} shell input tap {int(x)} {int(y)}", shell=True)

    def adb_hold(self, x, y, duration_seconds):
        """Simulates holding a finger in one spot (swipe with 0 distance)."""
        duration_ms = int(duration_seconds * 1000)
        subprocess.run(f"{ADB_CMD} -s {self.target_device} shell input swipe {int(x)} {int(y)} {int(x)} {int(y)} {duration_ms}", shell=True)
        # FORCE the thread to wait for the physical hold to finish before moving to the next step
        time.sleep(duration_seconds)

    def scale_coords(self, x, y, current_w, current_h):
        """Scales hardcoded JSON coordinates to fit whatever resolution the emulator is currently running at."""
        scale_x = current_w / self.original_res[0]
        scale_y = current_h / self.original_res[1]
        return int(x * scale_x), int(y * scale_y)

    def find_image_on_screen(self, screen, template_name, threshold=0.75):
        """Uses OpenCV to find a sub-image (template) inside the main screenshot."""
        if screen is None:
            return False, None
            
        current_h, current_w = screen.shape[:2]
        # Automatically choose the folder based on the emulator's current height (e.g., templates_1080)
        auto_template_dir = f"templates_{current_h}"
        template_path = os.path.join(auto_template_dir, template_name)
            
        if not os.path.exists(template_path):
            self.log(f"[!] FILE NOT FOUND: Cannot find '{template_path}'.")
            return False, None
            
        template = cv2.imread(template_path)
        if template is None:
            self.log(f"[!] CORRUPT IMAGE: '{template_path}' cannot be read by OpenCV.")
            return False, None
            
        # Perform template matching
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= threshold)
        
        # If any matches are found above the threshold (75% match default)
        if len(loc[0]) > 0:
            h, w = template.shape[:2]
            # Calculate the exact center pixel of the matched image
            center_x = loc[1][0] + w // 2
            center_y = loc[0][0] + h // 2
            return True, (center_x, center_y)
            
        return False, None

    def handle_step_failure(self, step):
        """
        Triggered when a step reaches its max_retries limit. 
        It taps the center of the screen to trigger Mobile UI, then searches 
        for an array of emergency images and KEEPS CLICKING them until they disappear.
        """
        on_fail = step.get('on_fail', 'continue')
        
        if on_fail == 'restart':
            wait_mins = step.get('fail_wait_minutes', 1)
            fail_images = step.get('fail_image', []) 
            
            # Safely handle both single strings and arrays of strings from the JSON
            if isinstance(fail_images, str):
                fail_images = [fail_images]
                
            fail_retries = step.get('fail_max_retries', 5) 
            fail_interval = step.get('fail_retry_interval', 1) 
            fail_skip = step.get('fail_skip', True) 
            
            self.log(f"[FAIL] Limit reached. Pausing for {wait_mins} min...")
            
            # Pause safely, checking every second if the user requested a script shutdown
            for _ in range(wait_mins * 60):
                if not self.is_running: return False
                time.sleep(1)
                
            if fail_images:
                # Force switch to Mobile UI by tapping the exact center of the screen
                self.log("[RESTART] Tapping screen center to force Mobile UI...")
                screen = self.capture_screen_adb()
                if screen is not None:
                    current_h, current_w = screen.shape[:2]
                    center_x = current_w // 2
                    center_y = current_h // 2
                    self.adb_click(center_x, center_y)
                    time.sleep(2) # Give the game time to change UI
                    
                self.log(f"[RESTART] Scanning for {len(fail_images)} possible emergency images...")
                attempts = 0
                found_and_clicked = False 
                
                # Try multiple times to find the emergency button
                while self.is_running:
                    attempts += 1
                    
                    if fail_skip and attempts > fail_retries:
                        break
                        
                    if fail_skip:
                        self.log(f"[RESTART] Attempt {attempts}/{fail_retries} -> Scanning...")
                    else:
                        self.log(f"[RESTART] Attempt {attempts}/∞ (Waiting...) -> Scanning...")
                        
                    screen = self.capture_screen_adb()
                    current_attempt_found = False
                    
                    # Iterate through every image in the array
                    for img_name in fail_images:
                        found, coords = self.find_image_on_screen(screen, img_name)
                        
                        if found:
                            self.log(f"[RESTART] Found '{img_name}'! Clicking {coords[0]}, {coords[1]}")
                            self.adb_click(coords[0], coords[1])
                            time.sleep(3) # Give it 3 seconds to process the UI change
                            current_attempt_found = True
                            found_and_clicked = True
                            break # Break the FOR loop to take a fresh screenshot
                    
                    if current_attempt_found:
                        # We clicked something! Use 'continue' to immediately loop again and take a fresh screenshot
                        continue
                        
                    if not current_attempt_found:
                        if found_and_clicked:
                            # We didn't find any images this time, BUT we clicked one previously. Success!
                            self.log("[RESTART] Recovery successful! Screen cleared.")
                            return True
                        else:
                            # We haven't found any images yet. Wait and try again.
                            time.sleep(fail_interval)
                
                if not found_and_clicked and fail_skip:
                    self.log(f"[RESTART] No emergency images were visible after {fail_retries} tries.")
            
            self.log("[RESTART] Restarting macro from Step 1...")
            return True # Returns True to tell the main loop to break and start over
            
        return False

    def run(self):
        """The main loop for this specific bot instance. Runs continuously until the script is closed."""
        self.log(f"Starting Macro: {self.flux.get('flux_name', 'Unnamed')}")
        
        while self.is_running: 
            self.log("Starting step execution...")
            restart_cycle = False # Flag used to break the FOR loop if an emergency restart is triggered
            
            # Loop through all instructions in flux.json
            for step_index, step in enumerate(self.flux['steps']):
                if not self.is_running: break
                
                action = step.get('action')
                skip = step.get('skip', False)
                self.log(f"Step {step_index + 1}/{len(self.flux['steps'])}: {action.upper()}")

                delay_before = step.get('delay_before', 0)
                if delay_before > 0:
                    self.log(f"Step {step_index + 1}: Waiting {delay_before}s...")
                    time.sleep(delay_before)
                
                if action == "find_and_click":
                    target_image = step['image']
                    interval = step.get('retry_interval', 1)
                    max_retries = step.get('max_retries', 1)
                    found = False
                    attempts = 0
                    
                    while not found and self.is_running:
                        attempts += 1
                        self.log(f"Step {step_index + 1}: Attempt {attempts}/{max_retries} -> {target_image}")
                        screen = self.capture_screen_adb()
                        found, coords = self.find_image_on_screen(screen, target_image)
                        
                        if found:
                            self.log(f"Step {step_index + 1}: [SUCCESS] Clicking {coords[0]}, {coords[1]}")
                            self.adb_click(coords[0], coords[1])
                        else:
                            # Failure logic handler
                            if attempts >= max_retries:
                                if step.get('on_fail') == 'restart':
                                    restart_cycle = self.handle_step_failure(step)
                                    break
                                elif skip:
                                    self.log(f"Step {step_index + 1}: [SKIP] Moving to next step.")
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
                    
                    while not found and self.is_running:
                        attempts += 1
                        self.log(f"Step {step_index + 1}: Attempt {attempts}/{max_retries} -> {target_image}")
                        screen = self.capture_screen_adb()
                        found, _ = self.find_image_on_screen(screen, target_image)
                        
                        if found:
                            # Scaling required here because coordinates are manually typed in JSON
                            current_h, current_w = screen.shape[:2]
                            scaled_x, scaled_y = self.scale_coords(t_x, t_y, current_w, current_h)
                            self.log(f"Step {step_index + 1}: [SUCCESS] Holding {scaled_x}, {scaled_y} for {hold_time}s")
                            self.adb_hold(scaled_x, scaled_y, hold_time)
                        else:
                            if attempts >= max_retries:
                                if step.get('on_fail') == 'restart':
                                    restart_cycle = self.handle_step_failure(step)
                                    break
                                elif skip:
                                    self.log(f"Step {step_index + 1}: [SKIP] Moving to next step.")
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
                    
                    while not found and self.is_running:
                        attempts += 1
                        self.log(f"Step {step_index + 1}: Attempt {attempts}/{max_retries} -> {target_image}")
                        screen = self.capture_screen_adb()
                        found, _ = self.find_image_on_screen(screen, target_image)
                        
                        if found:
                            current_h, current_w = screen.shape[:2]
                            scaled_sx, scaled_sy = self.scale_coords(s_x, s_y, current_w, current_h)
                            scaled_ex, scaled_ey = self.scale_coords(e_x, e_y, current_w, current_h)
                            self.log(f"Step {step_index + 1}: [SUCCESS] Dragging for {hold_time}s")
                            self.adb_drag_and_hold(scaled_sx, scaled_sy, scaled_ex, scaled_ey, hold_time)
                        else:
                            if attempts >= max_retries:
                                if step.get('on_fail') == 'restart':
                                    restart_cycle = self.handle_step_failure(step)
                                    break
                                elif skip:
                                    self.log(f"Step {step_index + 1}: [SKIP] Moving to next step.")
                                    break
                            time.sleep(interval)

                elif action == "wait_for_image":
                    target_image = step['image']
                    timeout = step.get('timeout', 60)
                    start_wait = time.time()
                    found = False
                    
                    while not found and (time.time() - start_wait) < timeout and self.is_running:
                        elapsed = int(time.time() - start_wait)
                        self.log(f"Step {step_index + 1}: Waiting {elapsed}s/{timeout}s -> {target_image}")
                        screen = self.capture_screen_adb()
                        found, _ = self.find_image_on_screen(screen, target_image)
                        if not found:
                            time.sleep(1)
                            
                    if found:
                        self.log(f"Step {step_index + 1}: [SUCCESS] Appeared!")
                    else:
                        if step.get('on_fail') == 'restart':
                            restart_cycle = self.handle_step_failure(step)
                            break
                        else:
                            self.log(f"Step {step_index + 1}: [TIMEOUT] Did not appear.")
                        
                elif action == "stop_script":
                    target_image = step['image']
                    self.log(f"Step {step_index + 1}: Safety check for {target_image}...")
                    screen = self.capture_screen_adb()
                    found, _ = self.find_image_on_screen(screen, target_image)
                    if found:
                        self.log(f"Step {step_index + 1}: [!!!] FATAL: Found {target_image}. Stopping.")
                        self.is_running = False
                        break
                    else:
                        self.log(f"Step {step_index + 1}: [CLEAR] Safety passed.")
                
                # If a fail sequence triggered 'restart', break out of the FOR loop (skipping remaining steps)
                if restart_cycle:
                    break
            
            # If a fail sequence triggered 'restart', we skip adding +1 to the cycle count
            # The 'continue' sends us straight back to the top of the 'while self.is_running' loop
            if restart_cycle:
                continue 
            
            # If the script reached this point, all steps were successfully executed.
            if self.is_running:
                self.cycle_count += 1
                emulator_cycles[self.name] = self.cycle_count
                save_cycle_count(self.name, self.cycle_count) # Save to PC
                self.log("Cycle completed successfully!")

# =======================================================
# MAIN EXECUTION
# =======================================================
if __name__ == "__main__":
    # 1. Load settings
    settings = load_settings()
    
    # 2. Load the JSON instruction list into memory once
    try:
        with open('flux.json', 'r') as f:
            flux_data = json.load(f)
    except FileNotFoundError:
        print("[!] Error: Could not find 'flux.json'.")
        sys.exit(1)

    print("\n[*] Resetting ADB Engine & Scanning for active emulators...")
    
    # 3. Find all running emulator instances
    active_devices = get_connected_devices()
    
    if not active_devices:
        print("\n[!!!] CRITICAL ERROR: Could not find any active emulator instances.")
        print("Ensure MuMu Player is open and 'ADB Debug/Root Permission' is enabled in settings.")
        input("\nPress Enter to close...")
        sys.exit(1)
    
    bots = []
    threads = []
    
    # 4. Create a Bot Instance and a Thread for EVERY emulator found
    for device_ip in active_devices:
        port = device_ip.split(':')[-1]
        
        # MAGIC HAPPENS HERE: Automatically grabs the Window Title!
        auto_name = get_emulator_name_from_port(port)
        emulator_statuses[auto_name] = "Waiting for initialization..."
        
        bot_instance = MacroBot(device_ip, settings, flux_data, auto_name)
        bots.append(bot_instance)
        
        # Start the bot loop in its own thread
        t = threading.Thread(target=bot_instance.run)
        t.daemon = True # Allows the thread to be killed instantly if the main script stops
        t.start()
        threads.append(t)

    # 5. Start the visual UI thread
    t_ui = threading.Thread(target=ui_loop)
    t_ui.daemon = True
    t_ui.start()

    # 6. Keep the main process alive so the threads can keep working
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # If the user presses Ctrl+C, shut down gracefully
        os.system('cls' if os.name == 'nt' else 'clear')
        print("\n[!] Script stopped manually by user (Ctrl+C). Shutting down all instances...")
        for bot in bots:
            bot.is_running = False # Flags all while loops to stop
        sys.exit(0)