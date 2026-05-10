from PyQt6.QtCore import QObject, pyqtSignal
import time
import threading
import warnings

class SignalManager(QObject):

    # Player window signals
    show_player_window = pyqtSignal()
    hide_player_window = pyqtSignal()
    reset_player_window = pyqtSignal()

    # Player control signals
    player_play = pyqtSignal()   # Start/play animation
    player_pause = pyqtSignal()  # Pause animation
    player_stop = pyqtSignal()   # Stop and reset animation

    stop_sound_player = pyqtSignal()

    # Oracle signals
    hide_oracle = pyqtSignal()
    show_oracle = pyqtSignal()
    change_oracle = pyqtSignal()
    change_oracle_previous = pyqtSignal()
    direct_oracle_change = pyqtSignal(str)
    oracle_position_changed = pyqtSignal(str)  # Emits position name (e.g., "Top Left")
    oracle_size_changed = pyqtSignal(int)

    # Transcription control
    is_transcribing = pyqtSignal(bool)

    enable_tray = pyqtSignal()
    disable_tray = pyqtSignal()

    enable_hands_free = pyqtSignal()
    disable_hands_free = pyqtSignal()
    hands_free_mode_changed = pyqtSignal(bool)

    # Push-to-talk signals (GUI -> Agent requests)
    push_to_talk_start = pyqtSignal()
    push_to_talk_stop = pyqtSignal()

    # Interrupt TTS signal (GUI -> Agent request)
    interrupt_tts = pyqtSignal()

    # STT state signals (Agent -> GUI confirmations)
    stt_ready = pyqtSignal()            # STT service fully initialized - safe to use PTT
    stt_capture_started = pyqtSignal()  # STT started capturing audio (PTT active)
    stt_capture_stopped = pyqtSignal()  # STT stopped capturing audio (PTT released)
    stt_hands_free_glow_on = pyqtSignal()
    stt_hands_free_glow_off = pyqtSignal()

    # Dictation signals (Agent -> GUI)
    dictation_started = pyqtSignal()
    dictation_stopped = pyqtSignal()

    # Dictation hotkey signals (GUI hotkey -> Agent)
    dictation_hotkey_pressed = pyqtSignal()
    dictation_hotkey_released = pyqtSignal()

    # Voice/action handler signals
    voice_set_is_listening = pyqtSignal(bool)

    # Chat signals
    chat_created = pyqtSignal(int)
    chat_updated = pyqtSignal(int)
    chat_cleared = pyqtSignal(int)
    chat_deleted = pyqtSignal(int)
    current_chat_changed = pyqtSignal(int)
    agent_context_updated = pyqtSignal(int)  # Chat ID when agent context (model/voice/persona) should sync

    exit_app = pyqtSignal()
    restart_app = pyqtSignal()
    show_about_window = pyqtSignal()

    # Telegram connection signals
    telegram_connected = pyqtSignal(str, str, int)  # (short_code, app_user_id, telegram_user_id)

    trigger_new_chat = pyqtSignal()

    # Chat streaming signals
    chat_stream_started = pyqtSignal(int)
    chat_stream_token = pyqtSignal(str)
    chat_stream_finished = pyqtSignal(int)
    chat_stream_error = pyqtSignal(str)
    chat_message_added = pyqtSignal(int, str, str)  # chat_id, role, content
    typing_indicator_changed = pyqtSignal(bool)
    # chat_id, status_text, done, clear_live_preview (clear web “live STT” bubble when utterance commits)
    transcription_progress = pyqtSignal(int, str, bool, bool)

    # Chat input signals (GUI -> Agent)
    # (text, is_telegram, uploaded_image_path, speak=None)
    send_text_input = pyqtSignal(str, bool, str, object)

    # Workflow execution signals
    step_waiting_for_feedback = pyqtSignal(int, int, int, str)            # step_id, workflow_id, run_id, result_text
    workflow_finished = pyqtSignal(int, str)                              # session_id, summary
    workflow_run_all_requested = pyqtSignal(int, object, object, str)     # session_id, steps_data, run_id, session_type
    workflow_execute_step_requested = pyqtSignal(int, int, str, object)   # step_id, session_id, instruction, chat_id
    workflow_cancel_requested = pyqtSignal(int)                           # session_id
    workflow_skip_step_requested = pyqtSignal(int)                        # session_id
    workflow_continue_requested = pyqtSignal(int, str)                    # session_id, optional_input

    # Deprecated step_runner_* signal names are forwarded to the workflow_*
    # signals above via __getattr__. They will be removed after one release cycle.
    # See _DEPRECATED_SIGNAL_ALIASES below.
    set_speaker_enabled = pyqtSignal(bool)

    # Web routes -> main thread
    web_send_to_agent_requested = pyqtSignal(int, str, bool, object, object)
    web_create_chat_emits_requested = pyqtSignal(int, str, bool, object, object, object, object)
    web_load_chat_in_agent_requested = pyqtSignal(int)

    # EULA
    eula_accepted = pyqtSignal()

    # Action recording signals
    action_recording_started = pyqtSignal(int)       # action_id when recording starts
    action_recording_stopped = pyqtSignal(int)       # action_id when recording stops

    # Action playback signals
    action_playback_finished = pyqtSignal()          # playback completed naturally
    action_playback_stopped = pyqtSignal(str)        # playback stopped/failed (reason)
    start_action_recording = pyqtSignal()            # Request to start recording
    start_action_recording_with_id = pyqtSignal(int)
    stop_action_recording = pyqtSignal()             # Request to stop recording
    waiting_for_action_name = pyqtSignal(int)
    set_action_name = pyqtSignal(int, str)

    # Action signals
    action_created = pyqtSignal(int)
    play_action_by_name = pyqtSignal(str)
    play_recording_file = pyqtSignal(str)
    speak_text_directly = pyqtSignal(str)

    # Step recording signals
    start_step_recording = pyqtSignal(int)   # step_id to start recording for
    stop_step_recording = pyqtSignal()
    step_recording_started = pyqtSignal(int)
    step_recording_stopped = pyqtSignal(int)

    # Workflow signals
    workflow_created = pyqtSignal(int)
    workflow_updated = pyqtSignal(int)
    workflow_deleted = pyqtSignal(int)
    workflow_node_created = pyqtSignal(int, str)
    workflow_node_updated = pyqtSignal(int, str)
    workflow_node_deleted = pyqtSignal(int, str)
    workflow_reload_required = pyqtSignal(int)

    # Agent reload
    reload_agent = pyqtSignal()
    agent_reload_started = pyqtSignal()  # Notification only: do not connect to reload_agent_session

    # Model hot-reload (change LLM model without restarting whole agent)
    model_hot_reload = pyqtSignal(str, str, object)  # provider, model_name, chat_id

    # Audio settings signals
    playback_speed_changed = pyqtSignal(float)
    speech_volume_changed = pyqtSignal(int)       # 0-100
    vad_threshold_changed = pyqtSignal(int)       # 0-100
    elevenlabs_voice_settings_changed = pyqtSignal(float, float, float, bool)
    audio_devices_changed = pyqtSignal(str, str)  # input_device, output_device
    audio_device_lists_updated = pyqtSignal(list, list)  # newly_added_outputs, newly_added_inputs
    stt_model_changed = pyqtSignal(str)

    # Files indexed notification
    files_indexed = pyqtSignal(str)

    # Mapping of deprecated step_runner_* signal names to their workflow_* replacements.
    # Kept for one release cycle to allow consumers to migrate.
    _DEPRECATED_SIGNAL_ALIASES = {
        "step_runner_run_all_requested": "workflow_run_all_requested",
        "step_runner_execute_requested": "workflow_execute_step_requested",
        "step_runner_cancel_requested": "workflow_cancel_requested",
        "step_runner_skip_step_requested": "workflow_skip_step_requested",
        "step_runner_continue_requested": "workflow_continue_requested",
    }

    def __getattr__(self, name: str):
        new_name = SignalManager._DEPRECATED_SIGNAL_ALIASES.get(name)
        if new_name is not None:
            warnings.warn(
                f"Signal '{name}' is deprecated, use '{new_name}' instead. "
                "This alias will be removed in the next release.",
                DeprecationWarning,
                stacklevel=2,
            )
            return getattr(self, new_name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __init__(self):
        super().__init__()
        self._is_transcribing = False
        self._chat_update_lock = False
        self._last_hide_player_window_emit_time = 0.0
        self._hide_player_window_emit_lock = threading.Lock()

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
                    pass

    def emit_chat_updated(self, chat_id):
        """Emit chat updated signal with recursion prevention"""
        if not self._chat_update_lock:
            try:
                self._chat_update_lock = True
                self.chat_updated.emit(chat_id)
            finally:
                self._chat_update_lock = False

    def emit_hide_player_window(self):
        """Emit hide_player_window signal with atomic duplicate prevention"""
        with self._hide_player_window_emit_lock:
            current_time = time.time()
            if current_time - self._last_hide_player_window_emit_time < 0.5:
                return
            self._last_hide_player_window_emit_time = current_time
        self.hide_player_window.emit()

signal_manager = SignalManager()


# Module-level event queue reference, set by AgentSession at startup.
# Used by speak_text_directly_event_queue() to route TTS text to the main process
# when running in the agent subprocess. Qt signals don't cross process boundaries,
# and without a running Qt event loop, cross-thread signal delivery is unreliable.
# The multiprocessing Queue (event_queue) works reliably across threads and processes.
_agent_event_queue = None
_last_speak_text = ""
_last_speak_ts = 0.0
_speak_dedup_lock = threading.Lock()


def set_agent_event_queue(event_queue):
    """Set the event queue reference for the agent subprocess.

    Called by AgentSession during startup so that speak_text_directly_event_queue()
    can route TTS text through the event_queue instead of relying on Qt signals.
    """
    global _agent_event_queue
    _agent_event_queue = event_queue


def speak_text_directly_event_queue(text: str):
    """Emit speak_text_directly through event_queue (preferred) or signal_manager (fallback).

    In the agent subprocess, Qt signals don't reach the main process.
    Use this instead of signal_manager.speak_text_directly.emit() from any code
    that runs in the agent subprocess (tools, LLM service, workflows, etc.).
    """
    global _agent_event_queue, _last_speak_text, _last_speak_ts
    message = (text or "").strip()
    if not message:
        return
    # Guardrail: suppress duplicate short-burst speaks to avoid double confirmations.
    with _speak_dedup_lock:
        now = time.time()
        if message == _last_speak_text and (now - _last_speak_ts) < 1.0:
            return
        _last_speak_text = message
        _last_speak_ts = now
    if _agent_event_queue:
        try:
            _agent_event_queue.put(('speak_text_directly', {'text': message}), block=False)
            return
        except Exception:
            pass
    # Fallback: signal_manager only works in the main process or same-thread
    # with DirectConnection in the agent subprocess.
    try:
        signal_manager.speak_text_directly.emit(message)
    except Exception:
        pass
