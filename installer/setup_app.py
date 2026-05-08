"""
Setup script for creating DecisionsAI macOS application bundle using py2app
"""
from setuptools import setup
import os

APP = ['bin/start.py']
DATA_FILES = [
    ('assets', ['assets']),
    ('distr', ['distr']),
    ('db', ['db']),
    ('README.md',),
    ('LICENSE.md',),
    ('requirements.txt',),
    ('info.plist',),
]

OPTIONS = {
    'argv_emulation': False,
    # pywhispercpp is bundled for default offline STT. VibeVoice is installed by bin/decisions.sh after pip (not in this file).
    # and omitted from the py2app bundle (large + upstream dep pins).
    'packages': [
        'PyQt6',
        'distr',
        'vosk',
        'ollama',
        'pipecat',
        'langchain',
        'langchain_community',
        'litellm',
        'torch',
        'torchaudio',
        'transformers',
        'numpy',
        'scipy',
        'sounddevice',
        'soundfile',
        'librosa',
        'kokoro_onnx',
        'pywhispercpp',
        'pynput',
        'pyautogui',
        'sqlalchemy',
        'beautifulsoup4',
        'lxml',
        'elevenlabs',
        'resampy',
        'syntok',
        'colorama',
    ],
    'includes': [
        'AppKit',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    'excludes': [
        'tkinter',
        'matplotlib',
        'pandas',
    ],
    'iconfile': None,  # Add icon path if you have one
    'plist': {
        'CFBundleName': 'DecisionsAI',
        'CFBundleDisplayName': 'DecisionsAI',
        'CFBundleGetInfoString': 'DecisionsAI - Voice-Controlled Digital Assistant',
        'CFBundleIdentifier': 'com.tensology.decisionsai',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': 'Copyright © 2024 Tensology (Pty) Ltd',
        'LSUIElement': '1',
        'NSHighResolutionCapable': True,
    },
}

setup(
    name='DecisionsAI',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)

