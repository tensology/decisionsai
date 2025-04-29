from distr.core.constants import ASSETS_DIR
from distr.core.signals import signal_manager
import subprocess
import threading
import os
import time


class SoundPlayer:
    def __init__(self):
        signal_manager.stop_sound_player.connect(self.stop_sound)
        self.sound_process = None
        self.sound_playing = False
        self.stop_event = threading.Event()
        self.show_voice_box = True

    def play_sound(self, sound_file, show_voice_box=True, is_speaking=True):
        if os.path.exists(sound_file):
            if not self.sound_playing:
                print(f"Playing sound: {sound_file}")
                self.sound_playing = True
                self.stop_event.clear()
                self.sound_process = subprocess.Popen(["afplay", sound_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                threading.Thread(target=self._monitor_sound_playback, args=(is_speaking,), daemon=True).start()
                if show_voice_box:
                    signal_manager.show_voice_box.emit()
                    signal_manager.sound_started.emit()
                if is_speaking:
                    signal_manager.voice_set_is_speaking.emit(True)
                    signal_manager.action_set_is_speaking.emit(True)
        else:
            print(f"Sound file not found: {sound_file}")

    def _monitor_sound_playback(self, is_speaking=True):
        while self.sound_process.poll() is None:
            if self.stop_event.is_set():
                if is_speaking:
                    signal_manager.voice_set_is_speaking.emit(False)
                    signal_manager.action_set_is_speaking.emit(False)
                self.sound_process.terminate()
                break
            time.sleep(0.1)
        self._reset_sound_state(False)
        signal_manager.sound_finished.emit()

        if is_speaking: 
            signal_manager.voice_set_is_speaking.emit(False)
            signal_manager.action_set_is_speaking.emit(False)

    def _reset_sound_state(self, is_speaking=False):
        self.sound_process = None
        self.sound_playing = False
        if is_speaking:
            signal_manager.voice_set_is_speaking.emit(False)
            signal_manager.action_set_is_speaking.emit(False)
        self.stop_event.clear()

    def stop_sound(self, is_speaking=False):
        if self.sound_process:
            self.stop_event.set()
            if is_speaking:
                signal_manager.voice_set_is_speaking.emit(False)
                signal_manager.action_set_is_speaking.emit(False)
            signal_manager.sound_finished.emit()

    def play_decisions_sound(self):
        sound_file = os.path.join(ASSETS_DIR, "sounds", "decisions.mp3")
        self.play_sound(sound_file, False, False)

    def is_sound_playing(self, is_speaking=True):
        if is_speaking:
            signal_manager.voice_set_is_speaking.emit(self.sound_playing)
            signal_manager.action_set_is_speaking.emit(self.sound_playing)
        return self.sound_playing
