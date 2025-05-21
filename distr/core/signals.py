from PyQt6.QtCore import QObject, pyqtSignal

class SignalManager(QObject):

    # VoiceBox-related signals
    update_player_window_position = pyqtSignal()

    show_player_window = pyqtSignal()
    hide_player_window = pyqtSignal()

    reset_player_window = pyqtSignal()

    # Sound-related signals
    sound_started = pyqtSignal()
    sound_stopped = pyqtSignal()
    sound_finished = pyqtSignal()

    stop_sound_player = pyqtSignal()

    hide_oracle = pyqtSignal() 
    show_oracle = pyqtSignal()  
    change_oracle = pyqtSignal() 

    #signals for transcription control
    is_transcribing = pyqtSignal(bool)
    is_listening = pyqtSignal(bool)
    is_speaking = pyqtSignal(bool)

    voice_update_last_speech_time = pyqtSignal()
    action_update_last_speech_time = pyqtSignal()

    enable_tray = pyqtSignal()
    disable_tray = pyqtSignal()

    #signals for transcription control
    voice_set_action = pyqtSignal(dict)
    voice_start_transcribing = pyqtSignal()
    voice_stop_transcribing = pyqtSignal()

    voice_set_is_transcribing = pyqtSignal(bool)
    voice_set_is_listening = pyqtSignal(bool)
    voice_set_is_speaking = pyqtSignal(bool)

    voice_stop_speaking = pyqtSignal()
    
    action_set_action = pyqtSignal(dict)

    action_set_is_transcribing = pyqtSignal(bool)
    action_set_is_listening = pyqtSignal(bool)
    action_set_is_speaking = pyqtSignal(bool)

    voice_set_transcription_buffer = pyqtSignal(list)
    action_set_transcription_buffer = pyqtSignal(list)
    

    # New signals for oracle color animations
    set_oracle_red = pyqtSignal()
    set_oracle_yellow = pyqtSignal()
    set_oracle_blue = pyqtSignal()
    set_oracle_green = pyqtSignal()
    set_oracle_white = pyqtSignal()
    reset_oracle_color = pyqtSignal()

    # New signals for chat operations
    chat_created = pyqtSignal(int)  # Emits new chat ID
    chat_updated = pyqtSignal(int)  # Emits updated chat ID
    chat_deleted = pyqtSignal(int)  # Emits deleted chat ID

    exit_app = pyqtSignal()  

    direct_oracle_change = pyqtSignal(str) 
    sync_oracle_selection = pyqtSignal(str)

    # Add new signal for triggering new chat
    trigger_new_chat = pyqtSignal()

    # Add new signals for oracle position and size
    oracle_position_changed = pyqtSignal(str)  # Emits position name (e.g., "Top Left")
    oracle_drag_started = pyqtSignal()  # New signal for drag events

    oracle_size_changed = pyqtSignal(int)  # Emits new size value

    # Add new signals for streaming
    chat_stream_started = pyqtSignal(int)  # Emits chat ID
    chat_stream_token = pyqtSignal(str)    # Emits each token
    chat_stream_finished = pyqtSignal(int)  # Emits chat ID
    chat_stream_error = pyqtSignal(str)    # Emits error message
    typing_indicator_changed = pyqtSignal(bool)  # Show/hide typing indicator

    # Add EULA-related signals
    eula_accepted = pyqtSignal()  # Emitted when user accepts the EULA
    eula_check_required = pyqtSignal(bool)  # Controls whether EULA check is required

    # Add this signal
    duck_playback = pyqtSignal(dict)  # or pyqtSignal(float, float, float, float)

    def __init__(self):
        super().__init__()
        self._is_transcribing = False
        self._chat_update_lock = False

    def set_is_transcribing(self, value):
        if self._is_transcribing != value:
            self._is_transcribing = value
            self.is_transcribing.emit(value)

    def get_is_transcribing(self):
        return self._is_transcribing

    def disconnect_all(self):
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if isinstance(attr, pyqtSignal):
                try:
                    attr.disconnect()
                except TypeError:
                    pass  # Signal was not connected

    def emit_chat_updated(self, chat_id):
        """Emit chat updated signal with recursion prevention"""
        if not self._chat_update_lock:
            try:
                self._chat_update_lock = True
                self.chat_updated.emit(chat_id)
            finally:
                self._chat_update_lock = False

signal_manager = SignalManager()