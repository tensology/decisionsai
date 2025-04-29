from pathlib import Path
import torch
from tqdm import tqdm
import hashlib
import torch.multiprocessing as mp
from torch.cuda import is_available as cuda_available
import os
from openai import OpenAI

class TextToSpeechProcessor:
    def __init__(self, model_name="tts_models/en/vctk/vits", output_dir="./playlist", speaker="p335"):
        """Initialize the TTS processor with specified model and output directory."""
        self.device = "cuda" if cuda_available() else "cpu"
        self.model_name = model_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.speaker = speaker
        
        # Initialize TTS model (using TTS library)
        try:
            from TTS.api import TTS
            self.tts = TTS(model_name).to(self.device)
        except ImportError:
            raise ImportError("Please install TTS: pip install TTS")

    def generate_uid(self, text):
        """Generate a unique identifier for a piece of text."""
        return hashlib.md5(text.encode()).hexdigest()[:8]

    def process_text_file(self, input_file="./text/doc.txt"):
        """Process the input text file and generate TTS files."""
        # Read the input file
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Input file not found: {input_file}")

        # Split into sentences
        sentences = split_into_sentences(text)
        total_sentences = len(sentences)

        print(f"Found {total_sentences} sentences to process")

        # Process each sentence with progress bar
        for idx, sentence in enumerate(tqdm(sentences, desc="Generating TTS files")):
            if not sentence.strip():
                continue

            # Generate filename
            uid = self.generate_uid(sentence)
            filename = f"{idx:04d}-{uid}.wav"
            output_path = self.output_dir / filename

            # Skip if file already exists
            if output_path.exists():
                continue

            try:
                # Generate TTS audio with speaker
                self.tts.tts_to_file(
                    text=sentence,
                    file_path=str(output_path),
                    speaker=self.speaker,
                    speed=1.0
                )
            except Exception as e:
                print(f"Error processing sentence {idx}: {e}")
                continue

        print(f"\nProcessing complete. Files saved to {self.output_dir}")
        return total_sentences

class OpenAITextToSpeechProcessor:
    def __init__(self, output_dir="./playlist", voice="alloy"):
        """Initialize the OpenAI TTS processor with specified output directory."""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Please install OpenAI: pip install openai")
            
        self.client = OpenAI(api_key="<your-api-key>") 
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.voice = voice  # Options: alloy, echo, fable, onyx, nova, shimmer
        
    def generate_uid(self, text):
        """Generate a unique identifier for a piece of text."""
        return hashlib.md5(text.encode()).hexdigest()[:8]

    def process_text_file(self, input_file="./text/doc.txt"):
        """Process the input text file and generate TTS files using OpenAI."""
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Input file not found: {input_file}")

        sentences = split_into_sentences(text)
        total_sentences = len(sentences)

        print(f"Found {total_sentences} sentences to process")

        for idx, sentence in enumerate(tqdm(sentences, desc="Generating TTS files")):
            if not sentence.strip():
                continue

            uid = self.generate_uid(sentence)
            filename = f"{idx:04d}-{uid}.mp3"
            output_path = self.output_dir / filename

            if output_path.exists():
                continue

            try:
                response = self.client.audio.speech.create(
                    model="tts-1",
                    voice=self.voice,
                    input=sentence
                )
                response.stream_to_file(str(output_path))
            except Exception as e:
                print(f"Error processing sentence {idx}: {e}")
                continue

        print(f"\nProcessing complete. Files saved to {self.output_dir}")
        return total_sentences

def split_into_sentences(text):
    """
    Split text into sentences using basic punctuation rules.
    For better results, consider using nltk.sent_tokenize
    """
    import re
    alphabets= "([A-Za-z])"
    prefixes = "(Mr|St|Mrs|Ms|Dr)[.]"
    suffixes = "(Inc|Ltd|Jr|Sr|Co)"
    starters = "(Mr|Mrs|Ms|Dr|He\s|She\s|It\s|They\s|Their\s|Our\s|We\s|But\s|However\s|That\s|This\s|Wherever)"
    acronyms = "([A-Z][.][A-Z][.](?:[A-Z][.])?)"
    websites = "[.](com|net|org|io|gov)"
    text = " " + text + "  "
    text = text.replace("\n"," ")
    text = re.sub(prefixes,"\\1<prd>",text)
    text = re.sub(websites,"<prd>\\1",text)
    if "Ph.D" in text: text = text.replace("Ph.D.","Ph<prd>D<prd>")
    text = re.sub("\s" + alphabets + "[.] "," \\1<prd> ",text)
    text = re.sub(acronyms+" "+starters,"\\1<stop> \\2",text)
    text = re.sub(alphabets + "[.]" + alphabets + "[.]" + alphabets + "[.]","\\1<prd>\\2<prd>\\3<prd>",text)
    text = re.sub(alphabets + "[.]" + alphabets + "[.]","\\1<prd>\\2<prd>",text)
    text = re.sub(" "+suffixes+"[.] "+starters," \\1<stop> \\2",text)
    text = re.sub(" "+suffixes+"[.]"," \\1<prd>",text)
    text = re.sub(" " + alphabets + "[.]"," \\1<prd>",text)
    if '"' in text: text = text.replace('."','".')
    if '"' in text: text = text.replace('."','".')
    if "!" in text: text = text.replace('!"','!')
    if "?" in text: text = text.replace('?"','?')
    text = text.replace(".",".<stop>")
    text = text.replace("?","?<stop>")
    text = text.replace("!","!<stop>")
    text = text.replace("<prd>",".")
    sentences = text.split("<stop>")
    sentences = [s.strip() for s in sentences]
    if sentences and not sentences[-1]: sentences = sentences[:-1]
    return sentences

def main():
    # Choose which processor to use
    use_openai = True  # Set to False to use the original TTS processor
    
    if use_openai:
        processor = OpenAITextToSpeechProcessor()
    else:
        processor = TextToSpeechProcessor()
        
    total_processed = processor.process_text_file()
    print(f"Successfully processed {total_processed} sentences")

if __name__ == "__main__":
    main() 