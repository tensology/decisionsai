import sounddevice as sd
import os
import time
import threading
import queue
import logging
from pathlib import Path
import soundfile as sf
import numpy as np


class Playback:
    def __init__(self, device_input=None, crossfade_duration=0.3):
        self.available_devices = self._get_input_devices()
        self.selected_device = None
        self.device_index = None
        
        # Playlist management
        self.playlist = []
        self.playlist_lock = threading.RLock()
        self.is_playing = False
        self.playback_thread = None
        self.stop_event = threading.Event()
        
        # Track played files to prevent duplicates
        self.played_files = set()
        self.played_files_lock = threading.RLock()
        
        # Crossfade settings
        self.crossfade_duration = crossfade_duration  # seconds
        
        # Volume control
        self.volume = 1.0
        self.normal_volume = 1.0  # Store normal volume level
        self.ducking_volume = 0.5  # Volume level when speech is detected (50%)
        self.is_ducking = False
        self.volume_lock = threading.RLock()
        
        # Configure logging
        self.logger = logging.getLogger("Playback")
        
        if device_input is not None:
            # Try to validate and use provided device
            if not self._validate_device(device_input):
                print(f"Warning: Device '{device_input}' not found or not valid for input")
                self.select_device()
        else:
            self.select_device()

    def _get_input_devices(self):
        """Get list of available input devices"""
        devices = sd.query_devices()
        input_devices = []
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                input_devices.append({
                    'index': i,
                    'name': device['name'],
                    'channels': device['max_input_channels'],
                    'default_samplerate': device['default_samplerate']
                })
        return input_devices

    def _validate_device(self, device_name):
        """Validate if a device exists and is valid for input"""
        for device in self.available_devices:
            if device['name'] == device_name:
                self.selected_device = device
                self.device_index = device['index']
                return True
        return False

    def select_device(self):
        """Interactive device selection"""
        print("\nAvailable input devices:")
        print("--------------------------------")
        for i, device in enumerate(self.available_devices):
            print(f"{i}: {device['name']}")
        print("--------------------------------")
        
        while True:
            try:
                selection = int(input("\nSelect input device number: "))
                if 0 <= selection < len(self.available_devices):
                    self.selected_device = self.available_devices[selection]
                    self.device_index = self.selected_device['index']
                    break
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a number.")

    def get_device_info(self):
        """Return selected device information"""
        if self.selected_device:
            return self.selected_device
        return None
        
    def add_to_playlist(self, file_path, auto_delete=True):
        """Add an audio file to the playlist"""
        if not os.path.exists(file_path):
            self.logger.warning(f"File not found: {file_path}")
            return False
            
        # Check if this file has already been played
        with self.played_files_lock:
            if file_path in self.played_files:
                self.logger.info(f"Skipping already played file: {file_path}")
                return False
                
        with self.playlist_lock:
            entry = {
                'file_path': file_path,
                'added_at': time.time(),
                'status': 'queued',
                'auto_delete': auto_delete,
                'played_at': None
            }
            self.playlist.append(entry)
            self.logger.info(f"Added to playlist: {file_path}")
            return True
            
    def add_tts_playlist(self, tts_engine):
        """Add all generated files from a TTSEngine to the playlist"""
        if not hasattr(tts_engine, 'get_playlist'):
            self.logger.error("TTSEngine does not have a get_playlist method")
            return False
            
        with self.playlist_lock:
            tts_playlist = tts_engine.get_playlist()
            added_count = 0
            
            for item in tts_playlist:
                if item.get('status') == 'generated' and item.get('file_path'):
                    file_path = item['file_path']
                    
                    # Check if this file has already been played
                    with self.played_files_lock:
                        if file_path in self.played_files:
                            continue
                            
                    entry = {
                        'file_path': file_path,
                        'added_at': time.time(),
                        'status': 'queued',
                        'auto_delete': True,  # Always set auto_delete to True for TTS files
                        'played_at': None
                    }
                    self.playlist.append(entry)
                    added_count += 1
                    
            self.logger.info(f"Added {added_count} files from TTSEngine to playlist")
            return added_count > 0
            
    def get_playlist(self):
        """Return a copy of the current playlist"""
        with self.playlist_lock:
            return self.playlist.copy()
            
    def clear_playlist(self):
        """Clear the playlist"""
        with self.playlist_lock:
            for item in self.playlist:
                if item.get('auto_delete'):
                    self._delete_file(item['file_path'])
            self.playlist.clear()
            self.logger.info("Playlist cleared")
            
    def play_playlist(self):
        """Play all files in the playlist from top to bottom"""
        if self.is_playing:
            self.logger.warning("Already playing, stopping current playback")
            self.stop_playback()
            
        # Start playback in a separate thread
        self.stop_event.clear()
        self.playback_thread = threading.Thread(target=self._playback_thread)
        self.playback_thread.daemon = True
        self.playback_thread.start()
        self.logger.info("Started playlist playback")
        
    def _playback_thread(self):
        """Thread that handles audio playback"""
        self.is_playing = True
        
        try:
            # Keep playing until all files are played or stop is requested
            while not self.stop_event.is_set():
                # Get the next file to play
                next_file = None
                with self.playlist_lock:
                    # Find the first queued file
                    for i, item in enumerate(self.playlist):
                        if item['status'] == 'queued':
                            next_file = item
                            # Mark as playing
                            item['status'] = 'playing'
                            item['played_at'] = time.time()
                            break
                
                # If no more files to play, exit the loop
                if next_file is None:
                    self.logger.info("No more files to play")
                    break
                
                # Play the file
                self.logger.info(f"Playing file: {next_file['file_path']}")
                try:
                    # Load and play the audio file
                    audio_data, samplerate = sf.read(next_file['file_path'])
                    
                    # Apply current volume setting
                    with self.volume_lock:
                        current_volume = self.volume
                    
                    # Apply volume to audio data
                    audio_data = audio_data * current_volume
                    
                    # Check if we need to crossfade with the next file
                    next_next_file = None
                    with self.playlist_lock:
                        # Find the next queued file after the current one
                        for i, item in enumerate(self.playlist):
                            if item['status'] == 'queued' and item['file_path'] != next_file['file_path']:
                                next_next_file = item
                                break
                    
                    if next_next_file and not self.stop_event.is_set():
                        # Load the next file for crossfading
                        try:
                            next_audio_data, next_samplerate = sf.read(next_next_file['file_path'])
                            
                            # Apply current volume to next audio data
                            with self.volume_lock:
                                current_volume = self.volume
                            next_audio_data = next_audio_data * current_volume
                            
                            # Check if sample rates match
                            if samplerate == next_samplerate:
                                # Calculate crossfade samples
                                crossfade_samples = int(self.crossfade_duration * samplerate)
                                
                                # Ensure we have enough samples for crossfade
                                if len(audio_data) >= crossfade_samples and len(next_audio_data) >= crossfade_samples:
                                    # Create crossfade
                                    fade_out = np.linspace(1.0, 0.0, crossfade_samples)
                                    fade_in = np.linspace(0.0, 1.0, crossfade_samples)
                                    
                                    # Apply crossfade
                                    audio_end = audio_data[-crossfade_samples:] * fade_out
                                    next_start = next_audio_data[:crossfade_samples] * fade_in
                                    
                                    # Combine the crossfaded parts
                                    crossfaded = audio_end + next_start
                                    
                                    # Create a single continuous audio stream
                                    continuous_audio = np.concatenate([
                                        audio_data[:-crossfade_samples],  # First part of current audio
                                        crossfaded,                        # Crossfaded part
                                        next_audio_data[crossfade_samples:]  # Rest of next audio
                                    ])
                                    
                                    # Play the continuous stream
                                    sd.play(continuous_audio, samplerate)
                                    sd.wait()
                                    
                                    # Mark both files as played
                                    with self.playlist_lock:
                                        for i, item in enumerate(self.playlist):
                                            if item['file_path'] in [next_file['file_path'], next_next_file['file_path']]:
                                                item['status'] = 'played'
                                    
                                    # Add both to played files set
                                    with self.played_files_lock:
                                        self.played_files.add(next_file['file_path'])
                                        self.played_files.add(next_next_file['file_path'])
                                    
                                    # Delete both files if auto_delete is True
                                    if next_file['auto_delete']:
                                        self._delete_file(next_file['file_path'])
                                    if next_next_file['auto_delete']:
                                        self._delete_file(next_next_file['file_path'])
                                    
                                    # Remove both from playlist
                                    with self.playlist_lock:
                                        for i, item in enumerate(self.playlist):
                                            if item['file_path'] in [next_file['file_path'], next_next_file['file_path']]:
                                                self.playlist.pop(i)
                                                self.logger.info(f"Removed from playlist: {item['file_path']}")
                                    
                                    # Skip to the next iteration
                                    continue
                                else:
                                    # Not enough samples for crossfade, play normally
                                    self.logger.info("Not enough samples for crossfade, playing normally")
                            else:
                                # Sample rates don't match, play normally
                                self.logger.info(f"Sample rates don't match ({samplerate} vs {next_samplerate}), playing normally")
                        except Exception as e:
                            self.logger.error(f"Error loading next file for crossfade: {e}")
                    
                    # If we get here, either there's no next file or crossfading failed
                    # Play the current file normally
                    sd.play(audio_data, samplerate)
                    sd.wait()  # Wait until the audio is finished playing
                    
                    # Mark as played
                    with self.playlist_lock:
                        for i, item in enumerate(self.playlist):
                            if item['file_path'] == next_file['file_path']:
                                item['status'] = 'played'
                                break
                    
                    # Add to played files set
                    with self.played_files_lock:
                        self.played_files.add(next_file['file_path'])
                    
                    # Delete the file if auto_delete is True
                    if next_file['auto_delete']:
                        self._delete_file(next_file['file_path'])
                        
                    # Remove from playlist
                    with self.playlist_lock:
                        for i, item in enumerate(self.playlist):
                            if item['file_path'] == next_file['file_path']:
                                self.playlist.pop(i)
                                self.logger.info(f"Removed from playlist: {next_file['file_path']}")
                                break
                                
                except Exception as e:
                    self.logger.error(f"Error playing file {next_file['file_path']}: {e}")
                    # Mark as error
                    with self.playlist_lock:
                        for i, item in enumerate(self.playlist):
                            if item['file_path'] == next_file['file_path']:
                                item['status'] = 'error'
                                break
                
                # Small delay to prevent CPU hogging
                time.sleep(0.1)
                
        except Exception as e:
            self.logger.error(f"Error in playback thread: {e}")
        finally:
            self.is_playing = False
            self.logger.info("Playback thread ended")
            
    def _delete_file(self, file_path):
        """Delete a file and log the result"""
        try:
            # Always delete the file from the tmp folder
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            pass

    def stop_playback(self):
        """Stop the current playback"""
        if not self.is_playing:
            return
            
        self.logger.info("Stopping playback")
        self.stop_event.set()
        
        # Stop any currently playing audio
        try:
            sd.stop()
        except Exception as e:
            self.logger.error(f"Error stopping audio playback: {e}")
        
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)
            
        self.is_playing = False
        self.logger.info("Playback stopped")
        
    def skip_current(self):
        """Skip the currently playing audio"""
        # Stop the current audio
        try:
            sd.stop()
        except Exception as e:
            self.logger.error(f"Error stopping current audio: {e}")
        
        self.logger.info("Skipping current audio")
        
    def get_current_playback(self):
        """Get information about what's currently playing"""
        with self.playlist_lock:
            for item in self.playlist:
                if item.get('status') == 'playing':
                    return item.copy()
        return None
        
    def set_volume(self, volume):
        """Set the playback volume (0.0 to 1.0)"""
        # Clamp volume between 0.0 and 1.0
        volume = max(0.0, min(1.0, volume))
        
        with self.volume_lock:
            self.volume = volume
            # Update normal volume if not currently ducking
            if not self.is_ducking:
                self.normal_volume = volume
            
        self.logger.info(f"Volume set to {volume}")
        
        # If we're currently playing, we need to stop and restart playback
        # to apply the new volume immediately
        if self.is_playing:
            self.logger.info("Restarting playback to apply new volume")
            # Get the current file being played
            current_file = self.get_current_playback()
            if current_file:
                # Stop current playback
                self.stop_playback()
                
                # Re-add the current file to the playlist
                with self.playlist_lock:
                    # Find the current file in the playlist
                    for i, item in enumerate(self.playlist):
                        if item['file_path'] == current_file['file_path']:
                            # Reset its status to queued
                            item['status'] = 'queued'
                            break
                
                # Restart playback
                self.play_playlist()
                
    def duck_volume(self, should_duck=True):
        """Duck (lower) the volume when user is speaking"""
        with self.volume_lock:
            if should_duck != self.is_ducking:
                self.is_ducking = should_duck
                
                # Apply appropriate volume level
                if should_duck:
                    # Apply ducking volume (50%)
                    self.volume = self.ducking_volume
                    self.logger.info(f"Ducking volume to {self.volume:.2f}")
                else:
                    # Restore normal volume
                    self.volume = self.normal_volume
                    self.logger.info(f"Restoring volume to {self.volume:.2f}")
                
                # If currently playing, apply volume change immediately
                if self.is_playing:
                    try:
                        # Get the current file being played
                        current_file = self.get_current_playback()
                        if current_file:
                            # Stop current playback
                            self.stop_playback()
                            
                            # Re-add the current file to the playlist
                            with self.playlist_lock:
                                # Find the current file in the playlist
                                for i, item in enumerate(self.playlist):
                                    if item['file_path'] == current_file['file_path']:
                                        # Reset its status to queued
                                        item['status'] = 'queued'
                                        break
                            
                            # Restart playback
                            self.play_playlist()
                    except Exception as e:
                        self.logger.error(f"Error applying volume change: {e}")

    def check_and_add_new_files(self, tts_engine):
        """Check for newly generated files from TTS engine and add them to playback immediately"""
        if not hasattr(tts_engine, 'get_playlist'):
            self.logger.error("TTSEngine does not have a get_playlist method")
            return 0
        
        # Force update TTS playlist from results to get the latest
        if hasattr(tts_engine, '_update_playlist_from_results'):
            tts_engine._update_playlist_from_results()
            
        added_count = 0
        with self.playlist_lock:
            tts_playlist = tts_engine.get_playlist()
            
            for item in tts_playlist:
                if item.get('status') == 'generated' and item.get('file_path'):
                    file_path = item['file_path']
                    
                    # Check if this file has already been played or queued
                    with self.played_files_lock:
                        if file_path in self.played_files:
                            continue
                            
                    # Check if already in our playlist
                    already_queued = False
                    for pb_item in self.playlist:
                        if pb_item.get('file_path') == file_path:
                            already_queued = True
                            break
                    
                    if not already_queued:
                        entry = {
                            'file_path': file_path,
                            'added_at': time.time(),
                            'status': 'queued',
                            'auto_delete': True,  # Always set auto_delete to True for TTS files
                            'played_at': None
                        }
                        self.playlist.append(entry)
                        added_count += 1
                        self.logger.info(f"Added new file to playback: {file_path}")
                        
        if added_count > 0 and not self.is_playing:
            self.play_playlist()
            
        return added_count



