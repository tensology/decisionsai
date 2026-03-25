#!/usr/bin/env python3
"""
Countdown timer script that automatically restarts ./bin/start.py
Supports interactive commands:
  - "yes" + enter: Restart immediately and reset countdown
  - A number: Set countdown to that many minutes, restart, and reset countdown
"""

import os
import sys
import time
import signal
import subprocess
import threading
from pathlib import Path

# Configuration: Set the initial countdown time in minutes
INITIAL_MINUTES = 4 * 60  # four hours

# Path to the application script (same directory as this script)
APP_SCRIPT = Path(__file__).parent / "start.py"
# Project root (parent of bin directory)
PROJECT_ROOT = Path(__file__).parent.parent


class AppManager:
    def __init__(self):
        self.process = None
        self.running = True
        self.countdown_seconds = INITIAL_MINUTES * 60
        self.original_countdown = INITIAL_MINUTES * 60
        
    def start_app(self):
        """Start the application in a subprocess"""
        if self.process and self.process.poll() is None:
            print("Application is already running.")
            return
        
        print(f"Starting application: {APP_SCRIPT}")
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(APP_SCRIPT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=PROJECT_ROOT
            )
            print(f"Application started with PID: {self.process.pid}")
        except Exception as e:
            print(f"Error starting application: {e}")
            
    def stop_app(self):
        """Force quit the application"""
        if not self.process:
            return
            
        if self.process.poll() is None:
            print(f"Stopping application (PID: {self.process.pid})...")
            try:
                # Try graceful termination first
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate
                    print("Application didn't terminate, forcing kill...")
                    self.process.kill()
                    self.process.wait()
            except Exception as e:
                print(f"Error stopping application: {e}")
            finally:
                self.process = None
                print("Application stopped.")
        else:
            print("Application is not running.")
            self.process = None
    
    def restart_app(self):
        """Stop and start the application"""
        self.stop_app()
        time.sleep(1)  # Brief pause between stop and start
        self.start_app()
    
    def format_time(self, seconds):
        """Format seconds as MM:SS"""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"
    
    def handle_input(self, user_input):
        """Handle user input commands"""
        user_input = user_input.strip().lower()
        
        if user_input == "yes":
            print("\nRestarting application immediately...")
            self.restart_app()
            self.countdown_seconds = self.original_countdown
            print(f"Countdown reset to {self.format_time(self.countdown_seconds)}")
            return True
        elif user_input.isdigit():
            minutes = int(user_input)
            self.original_countdown = minutes * 60
            self.countdown_seconds = minutes * 60
            print(f"\nSetting countdown to {minutes} minute(s)...")
            self.restart_app()
            print(f"Countdown reset to {self.format_time(self.countdown_seconds)}")
            return True
        
        return False
    
    def input_handler(self):
        """Handle user input in a separate thread"""
        while self.running:
            try:
                user_input = input()
                self.handle_input(user_input)
            except (EOFError, KeyboardInterrupt):
                break
    
    def run(self):
        """Main countdown loop"""
        print(f"Countdown Timer - Starting at {self.format_time(self.countdown_seconds)}")
        print("Commands:")
        print("  - Type 'yes' + Enter: Restart immediately and reset countdown")
        print("  - Type a number: Set countdown to that many minutes, restart, and reset")
        print("  - Ctrl+C: Exit\n")
        
        # Start the application
        self.start_app()
        
        # Start input handler thread
        input_thread = threading.Thread(target=self.input_handler, daemon=True)
        input_thread.start()
        
        try:
            while self.running:
                # Display countdown
                print(f"\rCountdown: {self.format_time(self.countdown_seconds)}", end="", flush=True)
                
                # Check if countdown reached zero
                if self.countdown_seconds <= 0:
                    print("\n\nCountdown reached zero. Restarting application...")
                    self.restart_app()
                    self.countdown_seconds = self.original_countdown
                    print(f"Countdown reset to {self.format_time(self.countdown_seconds)}")
                
                time.sleep(1)
                self.countdown_seconds -= 1
                
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self.running = False
            self.stop_app()
            sys.exit(0)


if __name__ == "__main__":
    # Verify the application script exists
    if not APP_SCRIPT.exists():
        print(f"Error: Application script not found at {APP_SCRIPT}")
        sys.exit(1)
    
    manager = AppManager()
    manager.run()

