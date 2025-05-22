# DecisionsAI

DecisionsAI is an intelligent digital assistant designed to understand and execute various tasks on your computer. It leverages cutting-edge AI technologies to provide voice interaction, automation, and adaptive learning capabilities.

> **IMPORTANT**: This project is currently in an experimental stage and not fully functional. It is actively being developed and updated. Contributions are deeply encouraged and welcome!

<p align="center">
  <img src="readme/example.png" alt="DecisionsAI UI">
</p>

> **WARNING**: This project currently requires significant memory resources to run. The current implementation uses the following models:
> - Whisper.cpp / Vosk speech recognition model (en-us-0.22)
> - Kokoro text-to-speech model
> - Ollama Gemma 3 4B language model
> - spaCy en_core_web_lg for NLP
>
> These models collectively consume approximately 6GB of memory. Therefore, it is strongly recommended to run DecisionsAI on a machine with a minimum of 16GB of RAM for optimal performance.
>
> **Current Limitations**:
> - Voice recognition is English-only
> - Some features may require internet connectivity
> - Chat interface is currently non-functional
> - Translation features are experimental and may be unstable
> - Dictation and transcription features may be unreliable

![DecisionsAI About](readme/about.png)

## Vision

Our vision is to develop an intelligent system assistant that:
- Understands and adapts to your computing environment
- Operates primarily offline with optional cloud capabilities
- Is capable of running on various hardware configurations
- Seamlessly integrates into your workflow without disruption
- Significantly enhances your productivity
- Promotes a more flexible work environment

## Current Features

- Voice-controlled AI assistant (English only)
- Task automation and computer control
- Natural language processing
- Text-to-speech capabilities
- Customizable actions and commands
- Multi-model AI support (currently Ollama only)

## Planned Features

- Chat interface for text-based interactions
- Additional AI model support (OpenAI, Anthropic)
- Raspberry Pi 5 compatibility
- Multilingual support
- Improved offline capabilities
- Enhanced dictation and transcription reliability

## System Requirements

- macOS (currently tested on Apple Silicon)
- Minimum 16GB RAM (32GB recommended)
- Python 3.8 or higher
- PortAudio and FFmpeg installed via Homebrew

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/tensology/decisionsai.git
   ```

2. Install system dependencies:
   ```bash
   brew install portaudio ffmpeg
   ```

3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the setup script to download the models:
   ```bash
   python setup.py
   ```

## Usage

1. Start the assistant:
   ```bash
   python start.py
   ```

2. Interact with the assistant using voice commands.

## Contributing

We welcome contributions to DecisionsAI! If you have suggestions or improvements, please open an issue or submit a pull request.

## Development Status

This project is actively being developed. Current focus areas include:
- Improving voice recognition accuracy
- Enhancing offline capabilities
- Optimizing memory usage
- Adding support for additional AI models
- Developing the chat interface

## Other Scripts

Using [Open-Interpreter](https://www.openinterpreter.com/), there's a script called `test_interpreter` that carries out tasks on your local machine:

```bash
python ./playground/test_interpreter.py
```

When prompted, try entering a command like:
"Create a new text file on my desktop called vegetables.txt and put a list of 15 vegetables in it. Then open the file."

## Voice Commands

DecisionsAI responds to a wide range of **__voice commands__**. 

Here's a comprehensive list of available commands:

### Navigation and Window Management

| Command | Description |
|---------|-------------|
| Open / Focus / Focus on | Open or focus on a specific window |
| Open file menu | Open the file menu |
| Hide oracle / Hide globe | Hide the oracle/globe interface |
| Show oracle / Show globe | Show the oracle/globe interface |
| Change oracle / Change globe | Change the oracle/globe interface |
| Open GPT | Open GPT (Alt+Space shortcut) |
| Open spotlight / Spotlight search | Open Spotlight search (Cmd+Space) |
| New tab | Create a new tab (Cmd+T) |
| Previous tab | Switch to the previous tab (Cmd+Alt+Left) |
| Next tab | Switch to the next tab (Cmd+Alt+Right) |
| Close | Close the current window (Cmd+W) |
| Quit | Quit the current application (Cmd+Q) |

### Text Editing and Navigation

| Command | Description |
|---------|-------------|
| Copy | Copy selected text (Cmd+C) |
| Paste | Paste copied text (Cmd+V) |
| Cut | Cut selected text (Cmd+X) |
| Select all | Select all text (Cmd+A) |
| Undo | Undo last action (Cmd+Z) |
| Redo | Redo last undone action (Cmd+Shift+Z) |
| Back space / Backspace | Delete character before cursor |
| Delete | Delete character after cursor |
| Clear line | Clear the current line |
| Delete line | Delete the entire line (Cmd+Shift+K) |
| Force delete | Force delete (Cmd+Backspace) |

### Carot Movement

| Command | Description |
|---------|-------------|
| Up / Down / Left / Right | Move cursor in specified direction |
| Page up / Page down | Scroll page up/down (Fn+Up/Down) |
| Home | Move cursor to beginning of line (Fn+Left) |
| End | Move cursor to end of line (Fn+Right) |

### Mouse Control

| Command | Description |
|---------|-------------|
| Mouse up / Mouse down / Mouse left / Mouse right | Move mouse in specified direction |
| Mouse slow up / Mouse slow down / Mouse slow left / Mouse slow right | Move mouse slowly in specified direction |
| Move mouse center | Move mouse to center of screen |
| Move mouse middle | Move mouse to horizontal middle of screen |
| Move mouse vertical middle | Move mouse to vertical middle of screen |
| Move mouse top | Move mouse to top of screen |
| Move mouse bottom | Move mouse to bottom of screen |
| Move mouse far left  | Move mouse to left edge of screen |
| Move mouse far right | Move mouse to right edge of screen |
| Right click | Perform a right-click |
| Click | Perform a left-click |
| Double click | Perform a double left-click |
| Scroll up / Scroll down | Scroll the page up/down |

### Sound Controls

| Command | Description |
|---------|-------------|
| Refresh / Reload | Refresh the current page (Cmd+R) |
| Pause / Stop / Play | Control media playback |
| Next track / Previous track | Switch between tracks |
| Mute | Mute audio |
| Volume up / Volume down | Adjust volume |

### Function Keys

| Command | Description |
|---------|-------------|
| Press F1 through Press F12 | Press the corresponding function key |

### Special Keys

| Command | Description |
|---------|-------------|
| Space bar / Space / Spacebar | Press the space bar |
| Control | Press the Control key |
| Command | Press the Command key |
| Enter this | Press the Enter key |
| Press alt / Alt | Press the Alt key |
| Press escape / Escape / Cancel | Press the Escape key |
| Tab | Press the Tab key |

### AI Assistant Interactions (EXPERIMENTAL)

| Command | Description |
|---------|-------------|
| Dictate | Start dictation mode, enters in whatever you say, except for ending phrases, ie. "Enter this" |
| Transcribe / Listen / Listen to | Start transcription mode, stores whatever you say to clipboard until you say "Enter this" or "stop listening" |
| Read / Speak / Recite / Announce | Read out the transcribed text, or if you say "this", it will read out whatever you've selected |
| Agent / Hey / Jarvis | Activate the AI agent for complex tasks |
| Explain / Elaborate | Explanation or elaboration of the copy that is in the clipboard |
| Calculate / Figure out / Analyze | Perform calculations or analysis of clipboard content |
| Translate | Translate text from source language to target language (experimental) |

### Control Commands

| Command | Description |
|---------|-------------|
| Start listening / Listen / Listen to | Begin voice command recognition |
| Stop listening / Stop / Halt | Stop voice command recognition |
| Stop speaking / Shut up / Be quiet | Stop the AI from speaking |
| Exit | Exit the application |

> **WARNING**: The AI assistant is still in development. Some features, particularly dictation, transcription, and translation, may be unstable or non-functional. Voice recognition is currently limited to English.

## License

This project is licensed under the TENSOLOGY COMMUNITY LICENSE AGREEMENT. See the [LICENSE.md](LICENSE.md) file for details.
