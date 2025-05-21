"""
Playback.py - Audio Playback Management System

This module provides a robust audio playback system with features including:
- Multi-threaded audio playback
- Crossfade support between tracks
- Volume control and ducking (ducking is when the volume is lowered when speech is detected)
- Automatic file cleanup
- Device management
- Playlist management
- Error handling and retry logic

The system uses sounddevice for audio output and soundfile for audio file reading,
with support for various audio formats and sample rates.

Key Features:
- Thread-safe playlist management
- Automatic sample rate normalization
- Crossfade support between tracks
- Volume control with smooth transitions
- Speech-aware volume ducking
- Automatic cleanup of temporary files
- Device selection and validation
- Error handling with retry logic

Class Organization:
1. Initialization and Setup
2. Device Management
3. Playlist Management
4. Playback Control
5. Volume Control
6. Resource Management
7. Utility Methods
"""

from typing import Dict, List, Optional, Any
from .utils import get_timestamp
import sounddevice as sd
import soundfile as sf
import numpy as np
import subprocess
import threading
import resampy
import logging
import atexit
import time
import os


class Playback:
    """
    Audio playback system with advanced features including crossfading, volume control,
    and automatic file management.
    
    This class manages the playback of audio files with support for:
    - Multi-threaded playback
    - Crossfading between tracks
    - Volume control with smooth transitions
    - Automatic file cleanup
    - Device management
    - Error handling and retry logic
    """
    
    # ===========================================
    # 1. Initialization and Setup
    # ===========================================
    def __init__(self, *args, **kwargs):
        """
        Initialize the playback system with device and settings.
        
        Args:
            output_device (str, optional): Name of the output device to use
            crossfade_duration (float): Duration of crossfade between tracks in seconds
            target_samplerate (int): Target sample rate for audio normalization
            blocksize (int): Audio buffer size for playback stability
            latency (str): Audio latency setting ('low', 'high')
            fade_in_duration (float): Duration of fade-in effect in seconds
            fade_out_duration (float): Duration of fade-out effect in seconds
            normalize_volume (bool): Whether to normalize volume between tracks
        """
        # Initialize logger
        self.logger = logging.getLogger(__name__)
        
        # Parse initialization parameters
        self.output_device = kwargs.get('output_device')
        self.crossfade_duration = kwargs.get('crossfade_duration', 0.3)
        self.target_samplerate = kwargs.get('target_samplerate', 44100)
        self.blocksize = kwargs.get('blocksize', 8192)
        self.latency = kwargs.get('latency', 'high')
        self.channels = kwargs.get('channels', 2)
        self.fade_in_duration = kwargs.get('fade_in_duration', 0.0)
        self.fade_out_duration = kwargs.get('fade_out_duration', 0.0)
        self.normalize_volume = kwargs.get('normalize_volume', False)
        self.chunk_duration = kwargs.get('chunk_duration', 0.1)  # Duration of each audio chunk in seconds
        
        # Initialize device management
        self.available_devices = self._get_output_devices()
        self.selected_device = None
        self.device_index = None
        
        # Initialize volume control with improved settings
        self.volume = 1.0
        self.target_volume = 1.0
        self.volume_step = 0.0
        self.volume_steps_remaining = 0
        self.volume_transition_duration = 2.0
        self.volume_step_interval = 0.1
        self.normal_volume = 1.0
        self.ducking_volume = 0.5
        self.is_ducking = False
        self.volume_lock = threading.RLock()
        self.system_volume = self._get_system_volume()
        self.volume_monitor_thread = None
        self._stop_volume_monitor = threading.Event()
        
        # Initialize playlist management
        self.playlist: List[Dict] = []
        self.lock = threading.Lock()
        self.processed_files = set()
        self.processed_files_lock = threading.Lock()
        
        # Initialize playback state
        self.is_playing = False
        self._stop_playback = threading.Event()
        self._playback_thread = None
        self._last_clear_time = 0
        
        # Initialize audio buffer and stream
        self._audio_buffer = None
        self._buffer_lock = threading.Lock()
        self.stream = None  # Initialize stream as None
        
        # Initialize audio system
        self._initialize_audio_system()
        
        # Device validation: ensure output device is valid
        if not self._validate_device(self.output_device):
            raise RuntimeError(f"Playback initialization failed: Output device '{self.output_device}' is not available. Please check your audio device settings.")
        
        # Start volume monitoring
        self._start_volume_monitor()
        
        # Register cleanup
        atexit.register(self.cleanup)
        
        # Initialize played blacklist
        self.played_sentence_ids = set()
        self.played_file_paths = set()
        self._blacklist_clear_timer = None
        # Ducking cancellation event
        self._ducking_cancel_event = threading.Event()
    
    def _initialize_audio_system(self):
        """Initialize the audio system with configured settings."""
        try:
            # Initialize PortAudio first
            if not sd._initialized:
                sd._initialize()
                self.logger.info("PortAudio initialized")
            
            sd.default.samplerate = self.target_samplerate
            sd.default.blocksize = self.blocksize
            sd.default.latency = self.latency
            
            # Get available devices first
            available_devices = self._get_output_devices()
            if not available_devices:
                raise RuntimeError("No output devices available")
            
            # If output_device is specified, validate it
            if self.output_device is not None:
                device_found = False
                for device in available_devices:
                    if device['name'] == self.output_device:
                        device_found = True
                        self.selected_device = device
                        self.device_index = device['index']
                        self.logger.info(f"Using specified device: {self.output_device}")
                        break
                
                if not device_found:
                    self.logger.warning(f"Warning: Device '{self.output_device}' not found")
                    # Use first available device
                    self.selected_device = available_devices[0]
                    self.device_index = self.selected_device['index']
                    self.output_device = self.selected_device['name']
                    self.logger.info(f"Using first available device: {self.output_device}")
            else:
                # No device specified, try to find MacBook Pro Speakers first
                macbook_speakers_found = False
                for device in available_devices:
                    if "MacBook Pro Speakers" in device['name']:
                        self.selected_device = device
                        self.device_index = device['index']
                        self.output_device = device['name']
                        self.logger.info(f"Using MacBook Pro Speakers: {self.output_device}")
                        macbook_speakers_found = True
                        break
                
                # If MacBook Pro Speakers not found, use first available device
                if not macbook_speakers_found:
                    self.selected_device = available_devices[0]
                    self.device_index = self.selected_device['index']
                    self.output_device = self.selected_device['name']
                    self.logger.info(f"Using first available device: {self.output_device}")
                
            self.logger.info("Sounddevice initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize sounddevice: {e}")
            raise

    def _get_output_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of available audio output devices.
        
        Returns:
            list: List of dictionaries containing device information
                  (index, name, channels, default_samplerate)
        """
        devices = sd.query_devices()
        output_devices = []
        for i, device in enumerate(devices):
            if device['max_output_channels'] > 0:
                output_devices.append({
                    'index': i,
                    'name': device['name'],
                    'channels': device['max_output_channels'],
                    'default_samplerate': device['default_samplerate']
                })
        return output_devices

    def _validate_device(self, device_name: str) -> bool:
        """
        Validate if a device exists and is valid for output.
        
        Args:
            device_name (str): Name of the device to validate
            
        Returns:
            bool: True if device is valid, False otherwise
        """
        for device in self._get_output_devices():
            if device['name'] == device_name:
                return True
        return False

    def select_device(self):
        """
        Interactive device selection through command line.
        Displays available devices and prompts user for selection.
        """
        self.logger.info("\nAvailable output devices:")
        self.logger.info("--------------------------------")
        for i, device in enumerate(self._get_output_devices()):
            self.logger.info(f"{i}: {device['name']}")
        self.logger.info("--------------------------------")
        
        # If output_device is already set, use it
        if self.output_device:
            for i, device in enumerate(self._get_output_devices()):
                if device['name'] == self.output_device:
                    self.logger.info(f"Using specified device: {self.output_device}")
                    return
            self.logger.warning(f"Specified device '{self.output_device}' not found, falling back to interactive selection")
        
        # Only do interactive selection if we're in an interactive context
        try:
            selection = int(input("\nSelect output device number: "))
            if 0 <= selection < len(self._get_output_devices()):
                self.output_device = self._get_output_devices()[selection]['name']
            else:
                self.logger.info("Invalid selection. Please try again.")
        except (EOFError, ValueError):
            # If we can't get interactive input, use the first available device
            if self._get_output_devices():
                self.output_device = self._get_output_devices()[0]['name']
                self.logger.info(f"Using first available device: {self.output_device}")
            else:
                self.logger.error("No output devices available")

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """
        Return selected device information.
        
        Returns:
            dict: Dictionary containing device information or None if no device selected
        """
        if self.output_device:
            for device in self._get_output_devices():
                if device['name'] == self.output_device:
                    return device
        return None

    # ===========================================
    # 3. Playlist Management
    # ===========================================
    def add_to_playlist(self, file_path: str) -> bool:
        """
        Add a file to the playlist if it hasn't been processed yet.
        
        Args:
            file_path (str): Path to the audio file to add
            
        Returns:
            bool: True if file was added, False otherwise
        """
        if not file_path or not os.path.exists(file_path):
            self.logger.error(f"Invalid file path: {file_path}")
            return False

        with self.processed_files_lock:
            if file_path in self.processed_files:
                return False
            self.processed_files.add(file_path)

        with self.lock:
            self.playlist.append({
                'file_path': file_path,
                'added_at': time.time(),
                'status': 'queued',
                'played': False
            })
            self.logger.info(f"Added file to playlist: {file_path}")
        return True

    def get_playlist(self) -> List[Dict]:
        """
        Return a copy of the current playlist.
        
        Returns:
            list: Copy of the current playlist
        """
        with self.lock:
            return self.playlist.copy()
            
    def clear_playlist(self):
        """
        Clear the playlist and cleanup any files marked for auto-deletion.
        """
        with self.lock:
            for item in self.playlist:
                if item.get('auto_delete'):
                    self._delete_file(item['file_path'])
            self.playlist.clear()
            self.logger.info("Playlist cleared")

    def queue_generated_tts_file(self, file_info: dict) -> bool:
        """
        Add a generated TTS file to the playlist if not already present.
        Args:
            file_info (dict): Dictionary with keys like 'id', 'text', 'file_path', 'position'.
        Returns:
            bool: True if file was added, False otherwise
        """
        if not file_info or not file_info.get('file_path') or not os.path.exists(file_info['file_path']):
            self.logger.error(f"Invalid TTS file info or file does not exist: {file_info}")
            return False
        with self.lock:
            existing_keys = set((item.get('sentence_id'), item.get('file_path')) for item in self.playlist)
            key = (file_info.get('id'), file_info.get('file_path'))
            if key in existing_keys:
                self.logger.debug(f"[DEBUG] Duplicate TTS file not added to playlist: {file_info.get('file_path')}")
                return False
            entry = {
                'sentence_id': file_info.get('id'),
                'file_path': file_info.get('file_path'),
                'position': file_info.get('position', 0),
                'status': 'generated',
                'sentence_group': file_info.get('sentence_group'),
                'is_played': False,
                'text': file_info.get('text'),
            }
            self.playlist.append(entry)
            self.logger.info(f"Queued TTS file for playback: {file_info.get('file_path')}")
            self.playlist.sort(key=lambda x: (x.get('sentence_group'), x.get('position', 0)))
        return True

    # ===========================================
    # 4. Playback Control
    # ===========================================
    def play_playlist(self):
        """Start playing the playlist with proper volume synchronization"""
        if not self.playlist:
            return
        if self.is_playing:
            return
        # Cancel ducking cut if in progress
        with self.volume_lock:
            if self.is_ducking and not self._ducking_cancel_event.is_set():
                self._ducking_cancel_event.set()
                self.is_ducking = False
                self.volume = self.normal_volume
                self.logger.info("Ducking cancelled due to new playback. Volume restored to normal.")
        # Ensure volume is properly synced before starting playback
        with self.volume_lock:
            current_system_volume = self._get_system_volume()
            if self.is_ducking:
                self.volume = current_system_volume * 0.5
            else:
                self.volume = current_system_volume
            self.system_volume = current_system_volume
            self.normal_volume = current_system_volume
            self.logger.info(f"Starting playback with volume: {self.volume:.2f} (system: {current_system_volume:.2f})")
            
        self.is_playing = True
        self._stop_playback.clear()
        
        # Start playback thread
        self._playback_thread = threading.Thread(target=self._playback_loop)
        self._playback_thread.daemon = True
        self._playback_thread.start()

    def start(self):
        """Start playback of the playlist"""
        if not self.playlist:
            self.logger.warning("Cannot start playback: playlist is empty")
            return
        if self.is_playing:
            self.logger.warning("Playback is already in progress")
            return
        # Cancel ducking cut if in progress
        with self.volume_lock:
            if self.is_ducking and not self._ducking_cancel_event.is_set():
                self._ducking_cancel_event.set()
                self.is_ducking = False
                self.volume = self.normal_volume
                self.logger.info("Ducking cancelled due to new playback. Volume restored to normal.")
        # Ensure volume is properly synced before starting playback
        with self.volume_lock:
            current_system_volume = self._get_system_volume()
            if self.is_ducking:
                self.volume = current_system_volume * 0.5
            else:
                self.volume = current_system_volume
            self.system_volume = current_system_volume
            self.normal_volume = current_system_volume
            self.logger.info(f"Starting playback with volume: {self.volume:.2f} (system: {current_system_volume:.2f})")
            
        self.is_playing = True
        self._stop_playback.clear()
        
        # Start playback thread
        self._playback_thread = threading.Thread(target=self._playback_loop)
        self._playback_thread.daemon = True
        self._playback_thread.start()

    def _playback_loop(self):
        """
        Main playback loop for playing audio files from the playlist.
        """
        last_playlist_empty = False
        self.logger.debug("[DEBUG] Playback loop started. Initial playlist: %s", [item.get('file_path') for item in self.playlist])
        while self.is_playing:
            try:
                self.logger.debug("[DEBUG] Top of playback loop. Playlist length: %d", len(self.playlist))
                if not self.playlist:
                    if not last_playlist_empty:
                        self.logger.debug("[DEBUG] Playlist is empty before popping.")
                        last_playlist_empty = True
                        # Schedule blacklist clear if playlist is empty and playback is not running
                        if not self.is_playing:
                            self._schedule_blacklist_clear()
                    time.sleep(0.05)
                    continue
                last_playlist_empty = False
                self.logger.debug("[DEBUG] Playlist before popping (full): %s", [item.get('file_path') for item in self.playlist])
                current_item = self.playlist[0]
                self.logger.debug("[DEBUG] Attempting to play file: %s", current_item.get('file_path'))
                audio_data, sample_rate = self._load_audio_file(current_item['file_path'])
                if audio_data is None or sample_rate is None:
                    self.logger.error(f"Failed to load audio file: {current_item['file_path']}")
                    # Remove the file from playlist and delete it if it exists
                    self._delete_file(current_item['file_path'])
                    with self.lock:
                        self.playlist.pop(0)
                        self.logger.debug("[DEBUG] Removed file after failed load. Playlist now: %s", [item.get('file_path') for item in self.playlist])
                    continue
                # Ensure volume is synced before playing
                with self.volume_lock:
                    current_system_volume = self._get_system_volume()
                    if self.is_ducking:
                        pass
                    else:
                        self.volume = current_system_volume
                # Play the audio with retry logic
                max_retries = 3
                retry_count = 0
                success = False
                while retry_count < max_retries and not success and not self._stop_playback.is_set():
                    self.logger.debug("[DEBUG] Playback attempt %d for file: %s", retry_count+1, current_item.get('file_path'))
                    success = self._play_audio_data(audio_data, sample_rate)
                    if not success:
                        retry_count += 1
                        self.logger.warning(f"Playback attempt {retry_count} failed for {current_item['file_path']}")
                        time.sleep(0.1)  # Brief pause between retries
                if success:
                    with self.lock:
                        if isinstance(current_item, dict):
                            current_item['played'] = True
                            current_item['played_at'] = time.time()
                        # Remove the played item from the playlist
                        if self.playlist and self.playlist[0].get('file_path') == current_item['file_path']:
                            self.logger.debug(f"[DEBUG] Playlist after popping: {[entry.get('file_path') for entry in self.playlist]}")
                            self.logger.info(f"Successfully played and removed: {current_item['file_path']}")
                            time.sleep(0.1)
                            # Add to blacklist
                            self.played_sentence_ids.add(current_item.get('sentence_id'))
                            self.played_file_paths.add(current_item.get('file_path'))
                            # Remove the item from the playlist
                            self.playlist.pop(0)
                            self.logger.debug("[DEBUG] Removed file after play. Playlist now: %s", [item.get('file_path') for item in self.playlist])
                            # Delete the file after playing
                            self._delete_file(current_item['file_path'])
                    # Prune all played files from the playlist
                    with self.lock:
                        before_prune = len(self.playlist)
                        self.playlist = [entry for entry in self.playlist if not entry.get('is_played', False)]
                        after_prune = len(self.playlist)
                        self.logger.debug(f"[DEBUG] Pruned played files from playlist. Before: {before_prune}, After: {after_prune}")
                        self.logger.debug("[DEBUG] Playlist after prune: %s", [item.get('file_path') for item in self.playlist])
                        self.playlist.sort(key=lambda x: (x.get('sentence_group'), x.get('position', 0)))
                    # If playlist is empty after popping, schedule blacklist clear
                    if not self.playlist:
                        self._schedule_blacklist_clear()
                else:
                    self.logger.error(f"Failed to play audio after {max_retries} attempts: {current_item['file_path']}")
                    if self.playlist and self.playlist[0].get('file_path') == current_item['file_path']:
                        self.playlist.pop(0)
                        self.logger.debug("[DEBUG] Removed file after failed play. Playlist now: %s", [item.get('file_path') for item in self.playlist])
                    # Delete the file if playback failed
                    self._delete_file(current_item['file_path'])
                # Log the full playlist after popping
                self.logger.debug("[DEBUG] Playlist after popping (full): %s", [item.get('file_path') for item in self.playlist])
            except Exception as e:
                self.logger.error(f"[DEBUG] Error in playback loop: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                time.sleep(0.05)
        self.logger.debug("[DEBUG] Playback loop exited. Final playlist: %s", [item.get('file_path') for item in self.playlist])
        time.sleep(0.2)
        self.is_playing = False
        self.logger.info("Playback loop ended")

    def stop_playback(self):
        """
        Stop the current playback.
        """
        self._stop_playback.set()
        self.is_playing = False

        if self._playback_thread and self._playback_thread.is_alive():
            try:
                self._playback_thread.join(timeout=1.0)
            except:
                pass
            self._playback_thread = None

    def get_current_playback(self):
        """
        Get information about the currently playing track.
        
        Returns:
            dict: Information about the current playback state
        """
        if not self.playlist:
            return None
            
        current_item = self.playlist[0]
        if isinstance(current_item, dict):
            current_track = current_item.get('file_path')
        else:
            current_track = current_item
            
        next_track = None
        if len(self.playlist) > 1:
            next_item = self.playlist[1]
            if isinstance(next_item, dict):
                next_track = next_item.get('file_path')
            else:
                next_track = next_item
                
        return {
            'is_playing': self.is_playing,
            'current_track': current_track,
            'next_track': next_track,
            'volume': self.volume
        }

    # ===========================================
    # 5. Volume Control
    # ===========================================
    def _get_device_volume(self) -> float:
        """Get the current volume of the selected output device"""
        try:
            if self.device_index is not None:
                # Get the current volume from the device we're using
                result = subprocess.run(['osascript', '-e', f'''
                    tell application "System Events"
                        tell process "System Settings"
                            set currentVolume to output volume of (get volume settings)
                        end tell
                    end tell
                    return currentVolume
                '''], capture_output=True, text=True)
                if result.returncode == 0:
                    return float(result.stdout.strip()) / 100.0
            return 1.0
        except Exception as e:
            self.logger.error(f"Error getting device volume: {e}")
            return 1.0

    def _set_device_volume(self, volume: float):
        """Set the volume of the selected output device"""
        try:
            if self.device_index is not None:
                volume_percent = int(volume * 100)
                # Set volume for the specific device we're using
                subprocess.run(['osascript', '-e', f'''
                    tell application "System Events"
                        tell process "System Settings"
                            set volume output volume {volume_percent}
                        end tell
                    end tell
                '''], capture_output=True)
        except Exception as e:
            self.logger.error(f"Error setting device volume: {e}")

    def _update_volume(self):
        """Update volume smoothly if a transition is in progress"""
        with self.volume_lock:
            if self.volume_steps_remaining > 0:
                self.volume += self.volume_step
                self.volume_steps_remaining -= 1
                if self.volume_steps_remaining == 0:
                    self.volume = self.target_volume
                return True
            return False

    def _start_volume_transition(self, target_volume: float):
        """Start a new volume transition"""
        with self.volume_lock:
            self.target_volume = target_volume
            steps = int(self.volume_transition_duration / self.volume_step_interval)
            self.volume_step = (target_volume - self.volume) / steps
            self.volume_steps_remaining = steps
            self.logger.info(f"Starting volume transition from {self.volume:.2f} to {target_volume:.2f}")

    def duck_volume(self, volume_ratio: float = 0.3, wait_time: float = 2.0, transition_duration: float = 1.0, fallout_duration: float = 1.0):
        """
        Duck volume to a specified ratio of current system volume, wait for specified time, then fade out to 0.
        
        Args:
            volume_ratio (float): Ratio of system volume to use during ducking (default: 0.3 or 30%)
            wait_time (float): Time in seconds to wait at ducked volume before fading out (default: 2.0)
            transition_duration (float): Duration of transition to ducked volume in seconds (default: 1.0)
            fallout_duration (float): Duration of fade-out to 0 in seconds (default: 1.0)
        """
        with self.volume_lock:
            if not self.is_ducking:
                # Immediately reset playback to prevent jumping
                self.logger.info("Ducking triggered: resetting playback (stop and clear playlist) to prevent jumping.")
                self.stop_playback()
                self.clear_playlist()
                self.is_ducking = True
                self._ducking_cancel_event.clear()
                # Calculate target volume based on current system volume
                target_volume = self.system_volume * volume_ratio
                self.logger.info(f"DUCKING: Volume set to {target_volume:.2f} ({volume_ratio*100:.0f}% of system volume)")
                
                # Set transition duration for initial ducking
                self.volume_transition_duration = transition_duration
                
                # Start volume transition to ducked level
                self._start_volume_transition(target_volume)
                
                # Start a thread to handle the delay and fade-out
                def delayed_stop():
                    # Wait for initial transition to complete
                    time.sleep(transition_duration)
                    
                    # Wait at ducked volume
                    time.sleep(wait_time)
                    
                    # Check if ducking was cancelled
                    if self._ducking_cancel_event.is_set():
                        self.logger.info("Ducking cut cancelled before fade-out due to new playback.")
                        return
                    
                    # Set transition duration for fade-out
                    self.volume_transition_duration = fallout_duration
                    
                    # Start fade-out to 0
                    self._start_volume_transition(0.0)
                    time.sleep(fallout_duration)  # Wait for fade-out to complete
                    
                    # Check again if ducking was cancelled during fade-out
                    if self._ducking_cancel_event.is_set():
                        self.logger.info("Ducking cut cancelled during fade-out due to new playback.")
                        return
                    
                    # Stop playback and clear playlist
                    self.stop_playback()
                    self.clear_playlist()
                    
                    # Reset volume and ducking state
                    with self.volume_lock:
                        self.volume = 0.0  # Ensure volume is at 0
                        self.is_ducking = False
                        # Don't restore to system volume here - let the volume monitor handle it
                
                stop_thread = threading.Thread(target=delayed_stop)
                stop_thread.daemon = True
                stop_thread.start()

    def _play_audio_data(self, audio_data, sample_rate):
        """Play audio data with enhanced crossfading and fade effects."""
        if audio_data is None or len(audio_data) == 0:
            self.logger.error("Invalid audio data: None or empty")
            return False
            
        if sample_rate is None:
            self.logger.error("Invalid sample rate: None")
            return False
            
        if not isinstance(audio_data, np.ndarray):
            self.logger.error("Invalid audio data type: not a numpy array")
            return False
            
        try:
            # Convert to float32 if needed
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)
            
            # Resample if needed
            if sample_rate != self.target_samplerate:
                audio_data = self._resample_audio(audio_data, sample_rate, self.target_samplerate)
                sample_rate = self.target_samplerate
            
            # Apply fade effects
            audio_data = self._apply_fade_effects(audio_data)
            
            # Calculate chunk size for smooth playback
            chunk_size = int(self.target_samplerate * self.chunk_duration)
            
            # Initialize stream if needed
            if self.stream is None:
                try:
                    self.stream = sd.OutputStream(
                        samplerate=self.target_samplerate,
                        channels=1,
                        dtype='float32',
                        device=self.output_device,
                        callback=None
                    )
                    self.stream.start()
                except Exception as e:
                    self.logger.error(f"Error initializing stream: {e}")
                    return False
            
            # Play audio in chunks with volume control
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if len(chunk) == 0:
                    continue
                
                # Apply current volume to the chunk
                with self.volume_lock:
                    current_volume = self.volume
                    if self.volume_steps_remaining > 0:
                        current_volume = self.volume + self.volume_step
                        self.volume = current_volume
                        self.volume_steps_remaining -= 1
                
                chunk = chunk * current_volume
                
                try:
                    self.stream.write(chunk)
                except Exception as e:
                    self.logger.error(f"Error writing to stream: {e}")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in _play_audio_data: {e}")
            return False

    def set_volume(self, volume: float):
        """Set the playback volume relative to system volume"""
        volume = max(0.0, min(1.0, volume))
        with self.volume_lock:
            if not self.is_ducking:
                # Calculate the ratio based on current system volume
                ratio = volume / self.system_volume if self.system_volume > 0 else 1.0
                self.volume = self.system_volume * ratio
                self.normal_volume = self.system_volume
                self.logger.info(f"Volume set to {self.volume:.2f} (ratio: {ratio:.2f} of system volume)")

    # ===========================================
    # 6. Resource Management
    # ===========================================
    def _schedule_file_deletion(self, file_path: str):
        """
        Schedule a file for deletion after a delay.
        
        Args:
            file_path (str): Path to the file to delete
        """
        def delayed_delete():
            time.sleep(1.0)
            self._delete_file(file_path)
            
        delete_thread = threading.Thread(target=delayed_delete)
        delete_thread.daemon = True
        delete_thread.start()
            
    def _delete_file(self, file_path: str):
        """
        Delete a file.
        
        Args:
            file_path (str): Path to the file to delete
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                self.logger.info(f"Deleted file: {file_path}")
        except Exception as e:
            self.logger.error(f"Error deleting file {file_path}: {e}")

    def _cleanup_sounddevice(self):
        """Clean up sounddevice resources and disable atexit handler"""
        try:
            # First stop volume monitoring to prevent access to audio resources
            self._stop_volume_monitor()
            
            # Stop playback and wait for thread to finish
            self.stop_playback()
            if self._playback_thread and self._playback_thread.is_alive():
                try:
                    self._playback_thread.join(timeout=1.0)
                except Exception as e:
                    self.logger.error(f"Error stopping playback thread: {e}")
            
            # Clear playlist and reset volume
            self.clear_playlist()
            self.set_volume(1.0)
            
            # Close audio stream with proper synchronization
            if hasattr(self, '_stream') and self._stream is not None:
                with self._buffer_lock:  # Ensure no audio data is being written
                    try:
                        self._stream.stop()
                        time.sleep(0.1)  # Give time for the stream to stop
                        self._stream.close()
                        self._stream = None
                    except Exception as e:
                        self.logger.error(f"Error closing audio stream: {e}")
            
            # Finally terminate PortAudio
            try:
                if sd._initialized:
                    sd._terminate()
                    self.logger.info("PortAudio resources explicitly terminated")
            except Exception as e:
                self.logger.error(f"Error terminating PortAudio: {e}")
            
            # Remove atexit handler
            if hasattr(atexit, '_exithandlers'):
                for handler in atexit._exithandlers:
                    if handler[0] == sd._terminate:
                        atexit._exithandlers.remove(handler)
                        break
            
        except Exception as e:
            self.logger.error(f"Error during sounddevice cleanup: {e}")
        finally:
            self.logger.info(f"[{get_timestamp()}] Playback resources cleaned up")

    def cleanup(self):
        """Clean up resources and stop playback."""
        try:
            # Stop playback if active
            if self.is_playing:
                self.stop()
            
            # Stop volume monitoring
            if self.volume_monitor_thread and self.volume_monitor_thread.is_alive():
                self._stop_volume_monitor.set()
                self.volume_monitor_thread.join(timeout=1.0)
            
            # Clean up stream
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception as e:
                    self.logger.error(f"Error cleaning up stream: {e}")
                finally:
                    self.stream = None
            
            # Clean up audio buffer
            with self._buffer_lock:
                self._audio_buffer = None
            
            # Clear playlist
            with self.lock:
                self.playlist.clear()
            
            # Clear processed files
            with self.processed_files_lock:
                self.processed_files.clear()
            
            self.logger.info("Playback cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup when object is garbage collected."""
        try:
            self.cleanup()
        except:
            pass

    # ===========================================
    # 7. Utility Methods
    # ===========================================
    def _start_playback_thread(self):
        """Start the playback thread"""
        # Ensure any existing thread is stopped
        if self._playback_thread and self._playback_thread.is_alive():
            self.logger.warning("Existing playback thread found, stopping it first")
            self._stop_playback.set()
            self._playback_thread.join(timeout=1.0)
            self._playback_thread = None
            
        # Clear the stop event and start new thread
        self._stop_playback.clear()
        self._playback_thread = threading.Thread(target=self._playback_loop)
        self.logger.info("Started playback thread")

    def check_and_add_new_files(self, tts_engine):
        """
        Check for new TTS files and add them to the playlist, deduplicating by (sentence_id, file_path).
        """
        if not hasattr(tts_engine, 'get_playlist'):
            return
        tts_playlist = tts_engine.get_playlist()
        with self.lock:
            existing_keys = set((item.get('sentence_id'), item.get('file_path')) for item in self.playlist)
            for entry in tts_playlist:
                key = (entry.get('sentence_id'), entry.get('file_path'))
                # Blacklist check
                if (entry.get('sentence_id') in self.played_sentence_ids or
                    entry.get('file_path') in self.played_file_paths):
                    self.logger.debug(f"[DEBUG] Blacklist: Skipping already played file: {entry.get('file_path')}")
                    continue
                if key not in existing_keys:
                    entry['is_played'] = False
                    self.playlist.append(entry)
                    self.logger.debug(f"[DEBUG] Added file to playlist: {entry.get('file_path')}")
                    existing_keys.add(key)
                else:
                    self.logger.debug(f"[DEBUG] Skipping duplicate file in playlist: {entry.get('file_path')}")
            self.playlist.sort(key=lambda x: (x.get('sentence_group'), x.get('position', 0)))

    def _load_audio_file(self, file_path):
        """Load audio file with error handling and logging"""
        try:
            if not os.path.exists(file_path):
                self.logger.error(f"Audio file not found: {file_path}")
                return None, None

            # Load audio file
            audio_data, sample_rate = sf.read(file_path)
            
            if audio_data is None or len(audio_data) == 0:
                self.logger.error(f"Failed to load audio data from file: {file_path}")
                return None, None

            if sample_rate is None:
                self.logger.error(f"Invalid sample rate in file: {file_path}")
                return None, None

            # Convert to mono if stereo
            if audio_data.ndim > 1:
                audio_data = np.mean(audio_data, axis=1)

            # Normalize audio data
            max_val = np.max(np.abs(audio_data))
            if max_val > 0:
                audio_data = audio_data / max_val

            self.logger.debug(f"Successfully loaded audio file: {file_path}")
            return audio_data, sample_rate

        except Exception as e:
            self.logger.error(f"Error loading audio file {file_path}: {e}")
            return None, None
            
    def _start_volume_monitor(self):
        """Start monitoring system volume changes"""
        def monitor_volume():
            while not self._stop_volume_monitor.is_set():
                try:
                    new_system_volume = self._get_system_volume()
                    if abs(new_system_volume - self.system_volume) > 0.01:
                        with self.volume_lock:
                            # Calculate the ratio of current volume to system volume
                            current_ratio = self.volume / self.system_volume if self.system_volume > 0 else 1.0
                            
                            # Update system volume
                            self.system_volume = new_system_volume
                            
                            # Apply the same ratio to the new system volume
                            if self.is_ducking:
                                # When ducking, maintain 50% of the current system volume
                                self.volume = new_system_volume * 0.5
                            else:
                                # When not ducking, maintain the same ratio
                                self.volume = new_system_volume * current_ratio
                            
                            # Update normal volume to match the ratio
                            self.normal_volume = new_system_volume
                            self.logger.info(f"Volume synced to system: {self.volume:.2f} (ratio: {current_ratio:.2f})")
                except Exception as e:
                    self.logger.error(f"Error monitoring system volume: {e}")
                time.sleep(0.05)  # More frequent checks for better sync
        
        self.volume_monitor_thread = threading.Thread(target=monitor_volume)
        self.volume_monitor_thread.daemon = True
        self.volume_monitor_thread.start()

    def _stop_volume_monitor(self):
        """Stop monitoring system volume changes"""
        if not hasattr(self, '_stop_volume_monitor'):
            return
            
        self._stop_volume_monitor.set()
        if self.volume_monitor_thread and self.volume_monitor_thread.is_alive():
            try:
                self.volume_monitor_thread.join(timeout=1.0)
            except Exception as e:
                self.logger.error(f"Error stopping volume monitor thread: {e}")
        self.volume_monitor_thread = None
        self._stop_volume_monitor.clear()  # Reset for potential future use

    def _get_system_volume(self) -> float:
        """Get the current system volume"""
        try:
            result = subprocess.run(['osascript', '-e', 'output volume of (get volume settings)'],
                                 capture_output=True, text=True)
            if result.returncode == 0:
                volume = float(result.stdout.strip()) / 100.0
                return max(0.0, min(1.0, volume))  # Ensure volume is between 0 and 1
            return 1.0
        except Exception as e:
            self.logger.error(f"Error getting system volume: {e}")
            return 1.0

    def _set_system_volume(self, volume: float):
        """Set the system volume directly"""
        try:
            volume_percent = int(volume * 100)
            subprocess.run(['osascript', '-e', f'set volume output volume {volume_percent}'],
                         capture_output=True)
        except Exception as e:
            self.logger.error(f"Error setting system volume: {e}")

    def _apply_volume_immediately(self, target_volume: float):
        """Apply volume change immediately without transition"""
        with self.volume_lock:
            self.volume = target_volume
            self.logger.info(f"Volume changed immediately to {self.volume:.2f}")

    def _resample_audio(self, audio_data, original_rate, target_rate):
        """Resample audio data to target sample rate"""
        try:
            if original_rate == target_rate:
                return audio_data
                
            # Calculate the number of samples in the resampled audio
            duration = len(audio_data) / original_rate
            new_length = int(duration * target_rate)
            
            # Resample using resampy
            resampled = resampy.resample(audio_data, original_rate, target_rate)
            
            # Ensure the resampled audio has the correct length
            if len(resampled) != new_length:
                resampled = resampled[:new_length]
                
            return resampled
            
        except Exception as e:
            self.logger.error(f"Error resampling audio: {e}")
            return None

    def _apply_fade_effects(self, audio_data):
        """Apply fade-in and fade-out effects to audio data"""
        try:
            if len(audio_data) == 0:
                return audio_data
                
            # Calculate fade lengths in samples
            fade_in_samples = int(self.fade_in_duration * self.target_samplerate)
            fade_out_samples = int(self.fade_out_duration * self.target_samplerate)
            
            # Create fade curves
            if fade_in_samples > 0:
                fade_in = np.linspace(0, 1, fade_in_samples)
                audio_data[:fade_in_samples] *= fade_in
                
            if fade_out_samples > 0:
                fade_out = np.linspace(1, 0, fade_out_samples)
                audio_data[-fade_out_samples:] *= fade_out
                
            return audio_data
            
        except Exception as e:
            self.logger.error(f"Error applying fade effects: {e}")
            return audio_data

    def _schedule_blacklist_clear(self, delay=60):
        if self._blacklist_clear_timer:
            self._blacklist_clear_timer.cancel()
        def clear_blacklist():
            self.played_sentence_ids.clear()
            self.played_file_paths.clear()
            self.logger.debug("[DEBUG] Cleared played blacklist.")
        self._blacklist_clear_timer = threading.Timer(delay, clear_blacklist)
        self._blacklist_clear_timer.start()