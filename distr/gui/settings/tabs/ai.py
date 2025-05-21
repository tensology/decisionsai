"""
AI Settings Tab Implementation

This module provides the AITab class which implements the AI settings tab
of the settings window. It handles AI provider configuration, model selection,
and TTS settings.

Key Features:
- Third-party API key management
- Text-to-Speech (TTS) configuration
- Large Language Model (LLM) settings
- Speech-to-Text (STT) settings
"""

from PyQt6 import QtWidgets, QtGui, QtCore
from distr.gui.settings.utils.settings import load_settings_from_db, save_settings_to_db
import logging

class AITab(QtWidgets.QWidget):
    """
    AI settings tab implementation.
    
    This class provides the UI and functionality for the AI settings tab,
    including provider configuration, model selection, and TTS settings.
    """

    def __init__(self, parent=None):
        """
        Initialize the AI settings tab.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
        self._load_settings()
        logging.debug("AITab initialized")

    def _setup_ui(self):
        """Set up the UI components."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(20)

        # Create a font for all group box titles
        title_font = QtGui.QFont()
        title_font.setPointSize(12)

        # Third Party API Keys section
        self._setup_api_keys_section(layout, title_font)
        
        # Text to Speech section (First in the pipeline)
        self._setup_tts_section(layout, title_font)
        
        # Large Language Models section (Second in the pipeline)
        self._setup_llm_section(layout, title_font)
        
        # Speech to Text section (Third in the pipeline)
        self._setup_stt_section(layout, title_font)

    def _setup_api_keys_section(self, parent_layout, title_font):
        """
        Set up the API keys section.
        
        Args:
            parent_layout (QLayout): Parent layout
            title_font (QFont): Title font
        """
        api_keys_group = QtWidgets.QGroupBox("Third Party API Keys")
        api_keys_group.setFont(title_font)
        api_keys_layout = QtWidgets.QGridLayout()
        api_keys_layout.setColumnStretch(1, 1)
        api_keys_layout.setColumnMinimumWidth(0, 120)
        api_keys_layout.setHorizontalSpacing(20)
        api_keys_layout.setVerticalSpacing(10)

        # Create provider checkboxes and inputs
        providers = ["AssemblyAI", "OpenAI", "Anthropic", "ElevenLabs"]
        
        # Add Ollama URL first (without checkbox)
        ollama_label = QtWidgets.QLabel("Ollama URL:")
        api_keys_layout.addWidget(ollama_label, 0, 0)
        self.ollama_input = QtWidgets.QLineEdit()
        self.ollama_input.setText("http://localhost:11434/")
        self.ollama_input.setPlaceholderText("Ollama URL")
        api_keys_layout.addWidget(self.ollama_input, 0, 1)
        
        # Add other providers with checkboxes
        for row, provider in enumerate(providers, start=1):
            checkbox = QtWidgets.QCheckBox(f"{provider}:")
            api_keys_layout.addWidget(checkbox, row, 0)

            attr_name = provider.lower().replace(" ", "_").replace("-", "")
            input_field = QtWidgets.QLineEdit()
            input_field.setPlaceholderText(f"Enter {provider} API Key")
            input_field.setEnabled(False)
                
            # Store references to checkboxes and inputs
            setattr(self, f"{attr_name}_checkbox", checkbox)
            setattr(self, f"{attr_name}_input", input_field)
            api_keys_layout.addWidget(input_field, row, 1)
            checkbox.stateChanged.connect(self.update_provider_inputs)

        api_keys_group.setLayout(api_keys_layout)
        parent_layout.addWidget(api_keys_group)

    def _setup_tts_section(self, parent_layout, title_font):
        """Set up the Text-to-Speech section."""
        tts_group = QtWidgets.QGroupBox("Text to Speech (TTS)")
        tts_group.setFont(title_font)
        tts_layout = QtWidgets.QVBoxLayout()

        # Provider and voice selection
        tts_provider_layout = QtWidgets.QHBoxLayout()
        self.tts_provider = QtWidgets.QComboBox()
        self.tts_voice = QtWidgets.QComboBox()
        self._update_tts_provider_options()
        self.tts_provider.currentTextChanged.connect(self.update_tts_voices)

        # Voice selection and play button layout
        voice_layout = QtWidgets.QHBoxLayout()
        voice_layout.addWidget(self.tts_voice)

        # Play button
        self.play_voice_button = QtWidgets.QPushButton()
        self.play_voice_button.setFixedSize(30, 30)
        self.play_voice_button.setStyleSheet("""
            QPushButton {
                background-color: black;
                border-radius: 15px;
                border: none;
            }
            QPushButton:hover {
                background-color: #333;
            }
        """)
        self.play_voice_button.setIcon(QtGui.QIcon.fromTheme("media-playback-start"))
        self.play_voice_button.setIconSize(QtCore.QSize(20, 20))
        self.play_voice_button.clicked.connect(self.play_selected_voice)
        voice_layout.addWidget(self.play_voice_button)

        tts_provider_layout.addWidget(QtWidgets.QLabel("Provider:"))
        tts_provider_layout.addWidget(self.tts_provider)
        tts_provider_layout.addWidget(QtWidgets.QLabel("Voice:"))
        tts_provider_layout.addLayout(voice_layout)
        tts_layout.addLayout(tts_provider_layout)

        tts_group.setLayout(tts_layout)
        parent_layout.addWidget(tts_group)

        # Connect ElevenLabs checkbox and input to provider update
        # (Do this after API keys section is set up)
        QtCore.QTimer.singleShot(0, self._connect_elevenlabs_signals)

    def _connect_elevenlabs_signals(self):
        # Connect signals for ElevenLabs checkbox and input
        if hasattr(self, 'elevenlabs_checkbox') and hasattr(self, 'elevenlabs_input'):
            self.elevenlabs_checkbox.stateChanged.connect(self._update_tts_provider_options)
            self.elevenlabs_input.textChanged.connect(self._update_tts_provider_options)

    def _is_elevenlabs_available(self):
        # Returns True if ElevenLabs is enabled and has a non-empty API key
        enabled = self.elevenlabs_checkbox.isChecked() if hasattr(self, 'elevenlabs_checkbox') else False
        key = self.elevenlabs_input.text().strip() if hasattr(self, 'elevenlabs_input') else ''
        return enabled and bool(key)

    def _update_tts_provider_options(self):
        # Save current selection
        current = self.tts_provider.currentText() if self.tts_provider.count() > 0 else None
        self.tts_provider.blockSignals(True)
        self.tts_provider.clear()
        self.tts_provider.addItem("Kokoro (Offline)")
        if self._is_elevenlabs_available():
            self.tts_provider.addItem("ElevenLabs (Online)")
        # Restore selection if possible, else default to Kokoro
        idx = self.tts_provider.findText(current)
        if idx >= 0:
            self.tts_provider.setCurrentIndex(idx)
        else:
            self.tts_provider.setCurrentIndex(0)
        self.tts_provider.blockSignals(False)
        self.update_tts_voices()

    def _setup_llm_section(self, parent_layout, title_font):
        """Set up the Large Language Models section."""
        llm_group = QtWidgets.QGroupBox("Large Language Models (LLM)")
        llm_group.setFont(title_font)
        llm_layout = QtWidgets.QVBoxLayout()

        # Conversational Agent
        conv_layout = QtWidgets.QHBoxLayout()
        conv_layout.addWidget(QtWidgets.QLabel("Conversational Agent:"))
        self.agent_provider = QtWidgets.QComboBox()
        self.agent_provider.addItems(["Ollama", "OpenAI", "Anthropic"])
        self.agent_provider.currentTextChanged.connect(self.update_agent_models)
        self.agent_model = QtWidgets.QComboBox()
        conv_layout.addWidget(self.agent_provider)
        conv_layout.addWidget(QtWidgets.QLabel("Model:"))
        conv_layout.addWidget(self.agent_model)
        llm_layout.addLayout(conv_layout)

        # Logic Agent
        logic_layout = QtWidgets.QHBoxLayout()
        logic_layout.addWidget(QtWidgets.QLabel("Logic Agent:"))
        self.code_provider = QtWidgets.QComboBox()
        self.code_provider.addItems(["Ollama", "OpenAI", "Anthropic"])
        self.code_provider.currentTextChanged.connect(self.update_code_models)
        self.code_model = QtWidgets.QComboBox()
        logic_layout.addWidget(self.code_provider)
        logic_layout.addWidget(QtWidgets.QLabel("Model:"))
        logic_layout.addWidget(self.code_model)
        llm_layout.addLayout(logic_layout)

        llm_group.setLayout(llm_layout)
        parent_layout.addWidget(llm_group)

    def _setup_stt_section(self, parent_layout, title_font):
        """Set up the Speech-to-Text section."""
        stt_group = QtWidgets.QGroupBox("Speech to Text (STT)")
        stt_group.setFont(title_font)
        stt_layout = QtWidgets.QVBoxLayout()

        # Model selection
        model_layout = QtWidgets.QHBoxLayout()
        model_layout.addWidget(QtWidgets.QLabel("Speech Recognition Model:"))
        self.input_speech_combo = QtWidgets.QComboBox()
        self.input_speech_combo.addItems([
            "Vosk (Local & Offline)",
            "Whisper.cpp (Local & Offline)",
            "AssemblyAI (Online)",
        ])
        model_layout.addWidget(self.input_speech_combo)
        stt_layout.addLayout(model_layout)

        # Sliders container
        sliders_layout = QtWidgets.QHBoxLayout()
        sliders_layout.setSpacing(20)

        # Playback speed slider
        speed_container = QtWidgets.QVBoxLayout()
        speed_container.setSpacing(5)
        speed_label = QtWidgets.QLabel("Playback Speed:")
        speed_label.setStyleSheet("font-weight: bold;")
        speed_container.addWidget(speed_label)
        
        speed_slider_layout = QtWidgets.QHBoxLayout()
        self.speed_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(1)  # 0.5x
        self.speed_slider.setMaximum(8)  # 4.0x
        self.speed_slider.setValue(2)     # 1.0x default
        self.speed_slider.setSingleStep(1)  # Step by 0.5
        
        slider_style = """
            QSlider::groove:horizontal {
                height: 4px;
                background: #d3d3d3;
                margin: 2px 0;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #999999;
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #f0f0f0;
                border: 1px solid #666666;
            }
        """
        self.speed_slider.setStyleSheet(slider_style)
        
        self.speed_label = QtWidgets.QLabel("1.0x")
        self.speed_label.setMinimumWidth(50)
        self.speed_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.speed_slider.valueChanged.connect(self.update_speed_label)
        
        speed_slider_layout.addWidget(self.speed_slider)
        speed_slider_layout.addWidget(self.speed_label)
        speed_container.addLayout(speed_slider_layout)
        sliders_layout.addLayout(speed_container)

        # VAD Threshold slider
        vad_container = QtWidgets.QVBoxLayout()
        vad_container.setSpacing(5)
        vad_label = QtWidgets.QLabel("VAD Threshold:")
        vad_label.setStyleSheet("font-weight: bold;")
        vad_container.addWidget(vad_label)
        
        vad_slider_layout = QtWidgets.QHBoxLayout()
        self.vad_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vad_slider.setMinimum(0)    # 0%
        self.vad_slider.setMaximum(100)  # 100%
        self.vad_slider.setValue(50)      # 50% default
        self.vad_slider.setSingleStep(1)
        self.vad_slider.setStyleSheet(slider_style)
        
        self.vad_label = QtWidgets.QLabel("50%")
        self.vad_label.setMinimumWidth(50)
        self.vad_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.vad_slider.valueChanged.connect(self.update_vad_label)
        
        vad_slider_layout.addWidget(self.vad_slider)
        vad_slider_layout.addWidget(self.vad_label)
        vad_container.addLayout(vad_slider_layout)
        sliders_layout.addLayout(vad_container)

        stt_layout.addLayout(sliders_layout)
        stt_group.setLayout(stt_layout)
        parent_layout.addWidget(stt_group)

    def _load_settings(self):
        """Load settings from database and update UI."""
        settings = load_settings_from_db()
        
        # Load provider settings
        for provider in ["assemblyai", "openai", "anthropic", "elevenlabs"]:
            checkbox = getattr(self, f"{provider}_checkbox")
            input_field = getattr(self, f"{provider}_input")
            enabled = settings.get(f"{provider}_enabled", False)
            checkbox.setChecked(enabled)
            input_field.setEnabled(enabled)
            value = settings.get(f"{provider}_key", "")
            input_field.setText(value)

        # Update TTS provider dropdown based on loaded state
        self._update_tts_provider_options()
        # Set the provider dropdown to the saved value if available
        provider_map = {'kokoro': 'Kokoro (Offline)', 'elevenlabs': 'ElevenLabs (Online)'}
        voice_provider = settings.get('voice_provider', 'kokoro')
        tts_provider = provider_map.get(voice_provider, 'Kokoro (Offline)')
        idx = self.tts_provider.findText(tts_provider)
        if idx >= 0:
            self.tts_provider.setCurrentIndex(idx)
        else:
            self.tts_provider.setCurrentIndex(0)
        # Populate voices for the selected provider
        self.update_tts_voices()
        # Set the voice dropdown to the saved value
        if voice_provider == 'kokoro':
            kokoro_voice = settings.get('kokoro_voice', 'af_heart')
            idx = self.tts_voice.findData(kokoro_voice)
            if idx < 0:
                idx = self.tts_voice.findText(kokoro_voice)
            if idx >= 0:
                self.tts_voice.setCurrentIndex(idx)
            else:
                self.tts_voice.setCurrentIndex(0)
        elif voice_provider == 'elevenlabs':
            elevenlabs_voice = settings.get('elevenlabs_voice', 'Jessica')
            idx = self.tts_voice.findData(elevenlabs_voice)
            if idx < 0:
                idx = self.tts_voice.findText(elevenlabs_voice)
            if idx >= 0:
                self.tts_voice.setCurrentIndex(idx)
            else:
                self.tts_voice.setCurrentIndex(0)
        else:
            self.tts_voice.setCurrentIndex(0)
        
        # Load Ollama URL
        ollama_url = settings.get('ollama_url', 'http://localhost:11434/')
        self.ollama_input.setText(ollama_url)
        
        # Load transcription model
        transcription_model = settings.get('transcription_model', 'Vosk (Local & Offline)')
        index = self.input_speech_combo.findText(transcription_model)
        if index >= 0:
            self.input_speech_combo.setCurrentIndex(index)
        
        # Load agent provider and model
        agent_provider = settings.get('agent_provider', 'Ollama')
        index = self.agent_provider.findText(agent_provider)
        if index >= 0:
            self.agent_provider.setCurrentIndex(index)
        
        # Load code provider and model
        code_provider = settings.get('code_provider', 'Ollama')
        index = self.code_provider.findText(code_provider)
        if index >= 0:
            self.code_provider.setCurrentIndex(index)
        
        # Load playback speed
        speed = settings.get('playback_speed', 1.0)
        slider_value = int(speed / 0.5)
        self.speed_slider.setValue(slider_value)
        self.speed_label.setText(f"{speed:.1f}x")

    def update_provider_inputs(self):
        """Update provider input fields based on checkbox states."""
        settings = load_settings_from_db()
        
        providers = {
            'assemblyai': ('assemblyai_enabled', 'assemblyai_key'),
            'openai': ('openai_enabled', 'openai_key'),
            'anthropic': ('anthropic_enabled', 'anthropic_key'),
            'elevenlabs': ('elevenlabs_enabled', 'elevenlabs_key')
        }
        
        # Get the provider that triggered the update (if any)
        sender = self.sender()
        if sender:
            provider_name = next((p for p in providers if f"{p}_checkbox" == sender.objectName()), None)
            if provider_name:
                input_field = getattr(self, f"{provider_name}_input")
                input_field.setEnabled(sender.isChecked())
                return

        # Initial setup or full refresh
        for provider, (enabled_field, key_field) in providers.items():
            checkbox = getattr(self, f"{provider}_checkbox")
            input_field = getattr(self, f"{provider}_input")
            
            # Set unique object name for identification
            checkbox.setObjectName(f"{provider}_checkbox")
            
            # Set the checkbox state from database
            is_enabled = settings.get(enabled_field, False)
            checkbox.setChecked(is_enabled)
            
            # Enable/disable input field based on checkbox
            input_field.setEnabled(is_enabled)
            
            # Set the saved value from database
            saved_value = settings.get(key_field, '')
            input_field.setText(saved_value)

    def update_tts_voices(self):
        """Update TTS voice options based on selected provider."""
        provider = self.tts_provider.currentText()
        self.tts_voice.clear()
        settings = load_settings_from_db()
        if provider == "Kokoro (Offline)":
            # Full Kokoro American English voices (from HuggingFace VOICES.md) with improved names
            voices = [
                {"id": "af_heart", "name": "Heart (Female, Highest Quality)"},
                {"id": "af_alloy", "name": "Alloy (Female, Good Quality)"},
                {"id": "af_aoede", "name": "Aoede (Female, Good Quality)"},
                {"id": "af_bella", "name": "Bella (Female, Highest Quality)"},
                {"id": "af_jessica", "name": "Jessica (Female, Fair Quality)"},
                {"id": "af_kore", "name": "Kore (Female, Good Quality)"},
                {"id": "af_nicole", "name": "Nicole (Female, Good Quality)"},
                {"id": "af_nova", "name": "Nova (Female, Fair Quality)"},
                {"id": "af_river", "name": "River (Female, Fair Quality)"},
                {"id": "af_sarah", "name": "Sarah (Female, Good Quality)"},
                {"id": "af_sky", "name": "Sky (Female, Basic Quality)"},
                {"id": "am_adam", "name": "Adam (Male, Low Quality)"},
                {"id": "am_echo", "name": "Echo (Male, Fair Quality)"},
                {"id": "am_eric", "name": "Eric (Male, Fair Quality)"},
                {"id": "am_fenrir", "name": "Fenrir (Male, Good Quality)"},
                {"id": "am_liam", "name": "Liam (Male, Fair Quality)"},
                {"id": "am_michael", "name": "Michael (Male, Good Quality)"},
                {"id": "am_onyx", "name": "Onyx (Male, Fair Quality)"},
                {"id": "am_puck", "name": "Puck (Male, Good Quality)"},
                {"id": "am_santa", "name": "Santa (Male, Basic Quality)"},
            ]
            for voice in voices:
                self.tts_voice.addItem(voice["name"], voice["id"])
            # Select saved voice
            kokoro_voice = settings.get('kokoro_voice', 'af_heart')
            idx = self.tts_voice.findData(kokoro_voice)
            if idx < 0:
                idx = self.tts_voice.findText(kokoro_voice)
            if idx >= 0:
                self.tts_voice.setCurrentIndex(idx)
            else:
                self.tts_voice.setCurrentIndex(0)
        elif provider == "ElevenLabs (Online)":
            # Dynamically fetch voices from ElevenLabs using the saved key
            elevenlabs_key = settings.get('elevenlabs_key', '')
            if not elevenlabs_key:
                QtWidgets.QMessageBox.warning(self, "ElevenLabs Key Missing", "No ElevenLabs API key found in settings.")
                return
            try:
                from elevenlabs import ElevenLabs
                print(f"[DEBUG] ElevenLabs API key before loading library: '{elevenlabs_key}'")
                client = ElevenLabs(api_key=elevenlabs_key)
                voices = client.voices.get_all().voices
                if not voices:
                    QtWidgets.QMessageBox.warning(self, "No Voices", "No voices found in ElevenLabs account.")
                    return
                for voice in voices:
                    self.tts_voice.addItem(voice.name, voice.voice_id)
                # Select saved voice
                elevenlabs_voice = settings.get('elevenlabs_voice', voices[0].voice_id)
                idx = self.tts_voice.findData(elevenlabs_voice)
                if idx < 0:
                    idx = self.tts_voice.findText(elevenlabs_voice)
                if idx >= 0:
                    self.tts_voice.setCurrentIndex(idx)
                else:
                    self.tts_voice.setCurrentIndex(0)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "ElevenLabs Error", f"Failed to load ElevenLabs voices.\n{e}")
                self.tts_voice.clear()
        else:
            self.tts_voice.clear()

    def update_agent_models(self):
        """Update agent model options based on selected provider."""
        provider = self.agent_provider.currentText()
        self.agent_model.clear()
        
        if provider == "Ollama":
            models = [{"id":"llama2", "name":"Llama 2"}, {"id":"mistral", "name":"Mistral"}]
        elif provider == "OpenAI":
            models = [{"id":"gpt3", "name":"GPT-3.5"}, {"id":"gpt4", "name":"GPT-4"}]
        elif provider == "Anthropic":
            models = [{"id":"claude3", "name":"Claude 3 Opus"}, {"id":"claude3-s", "name":"Claude 3 Sonnet"}]
        else:
            models = []
        
        for model in models:
            self.agent_model.addItem(model["name"], model["id"])

    def update_code_models(self):
        """Update code model options based on selected provider."""
        provider = self.code_provider.currentText()
        self.code_model.clear()
        
        if provider == "Ollama":
            models = [{"id":"codellama", "name":"CodeLlama"}, {"id":"deepseek", "name":"DeepSeek Coder"}]
        elif provider == "OpenAI":
            models = [{"id":"gpt4", "name":"GPT-4"}, {"id":"gpt3", "name":"GPT-3.5"}]
        elif provider == "Anthropic":
            models = [{"id":"claude3", "name":"Claude 3 Opus"}, {"id":"claude3-s", "name":"Claude 3 Sonnet"}]
        else:
            models = []
        
        for model in models:
            self.code_model.addItem(model["name"], model["id"])

    def update_speed_label(self, value):
        """Update speed label when slider changes."""
        speed = value * 0.5
        self.speed_label.setText(f"{speed:.1f}x")
        
        settings = load_settings_from_db()
        settings['playback_speed'] = speed
        save_settings_to_db(settings)

    def play_selected_voice(self):
        """Play a sample of the selected voice."""
        selected_voice = self.tts_voice.currentText()
        logging.debug(f"Playing sample of voice: {selected_voice}")
        # Implement the logic to play a sample of the selected voice 

    def update_vad_label(self, value):
        """Update VAD threshold label when slider changes."""
        self.vad_label.setText(f"{value}%")
        
        settings = load_settings_from_db()
        settings['vad_threshold'] = value
        save_settings_to_db(settings) 