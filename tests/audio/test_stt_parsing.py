
import sys
import unittest
import logging

# Mock logging
logging.basicConfig(level=logging.ERROR)

class TestSTTConfigParsing(unittest.TestCase):
    def test_parse_openai_model(self):
        """Test parsing of OpenAI Whisper model string"""
        transcription_model = "OpenAI Whisper (whisper-1)"
        config = {'stt': {}}
        
        # Logic from session.py
        if 'OpenAI Whisper' in transcription_model:
            config['stt']['engine'] = 'openai_whisper'
            if '(' in transcription_model and ')' in transcription_model:
                start = transcription_model.find('(') + 1
                end = transcription_model.find(')')
                model_part = transcription_model[start:end]
                config['stt']['model'] = model_part if 'whisper' in model_part else 'whisper-1'
            else:
                config['stt']['model'] = 'whisper-1'
                
        self.assertEqual(config['stt']['engine'], 'openai_whisper')
        self.assertEqual(config['stt']['model'], 'whisper-1')

    def test_parse_openai_model_future(self):
        """Test parsing of a hypothetical future OpenAI model"""
        transcription_model = "OpenAI Whisper (whisper-large-v3)"
        config = {'stt': {}}
        
        # Logic from session.py
        if 'OpenAI Whisper' in transcription_model:
            config['stt']['engine'] = 'openai_whisper'
            if '(' in transcription_model and ')' in transcription_model:
                start = transcription_model.find('(') + 1
                end = transcription_model.find(')')
                model_part = transcription_model[start:end]
                config['stt']['model'] = model_part if 'whisper' in model_part else 'whisper-1'
            else:
                config['stt']['model'] = 'whisper-1'
                
        self.assertEqual(config['stt']['engine'], 'openai_whisper')
        self.assertEqual(config['stt']['model'], 'whisper-large-v3')

    def test_parse_assemblyai_model(self):
        """Test parsing of AssemblyAI model string"""
        transcription_model = "AssemblyAI Universal-2"
        config = {'stt': {}}
        
        # Logic from session.py
        if 'AssemblyAI' in transcription_model:
            config['stt']['engine'] = 'assemblyai'
            if 'Universal-2' in transcription_model:
                config['stt']['model'] = 'universal-2'
            elif 'SLAM-1' in transcription_model:
                config['stt']['model'] = 'slam-1'
            else:
                config['stt']['model'] = 'universal-2'
                
        self.assertEqual(config['stt']['engine'], 'assemblyai')
        self.assertEqual(config['stt']['model'], 'universal-2')

if __name__ == '__main__':
    unittest.main()
