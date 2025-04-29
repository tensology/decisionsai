# from sentence_transformers import SentenceTransformer
from distr.core.utils import load_actions_config
from distr.core.signals import signal_manager
from fuzzywuzzy import fuzz
import importlib
import logging
import pyaudio
import torch
import time

class ActionHandler:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # this is the list of actions that the action handler can execute
        # comes from /distr/core/actions.config.json
        self.actions = self.load_actions()
        self.action = {} # stores the current action being executed
        self.previous_action = {} 

        # this model is used to compare the similarity of the input text to the trigger words
        # self.model = SentenceTransformer('all-MiniLM-L6-v2', device="cpu")
        self.model = None
        self.trigger_words, self.trigger_descriptions = self.load_triggers()

        self.is_listening = True
        self.is_transcribing = False
        self.is_speaking = False

        self.last_speech_time = None

        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.frames = []
        self.audio_filename = None
        self.transcription_buffer = []

        # connect signals
        signal_manager.action_set_is_transcribing.connect(self.set_is_transcribing)
        signal_manager.action_set_is_listening.connect(self.set_is_listening)
        signal_manager.action_set_is_speaking.connect(self.set_is_speaking)

        signal_manager.action_update_last_speech_time.connect(self.update_last_speech_time)
        signal_manager.action_set_action.connect(self.set_action)

        self.is_running = True  

    def set_action(self, action):
        self.previous_action = self.action
        self.action = action

    def set_transcription_buffer(self, buffer):
        self.transcription_buffer = buffer        

    def load_triggers(self):
        config = load_actions_config()
        trigger_words = []
        trigger_descriptions = {}

        for action in config["actions"]:
            if "trigger" in action:
                trigger_words.append(action["trigger"])
                trigger_descriptions[action["trigger"]] = action.get("method", "")
                
                # Include trigger variants
                if "trigger_variants" in action:
                    trigger_words.extend(action["trigger_variants"])
                    for variant in action["trigger_variants"]:
                        trigger_descriptions[variant] = action.get("method", "")

        return trigger_words, trigger_descriptions

    def load_actions(self):
        config = load_actions_config()
        return config["actions"]


    def find_action(self, input_text, threshold=0.5): # get the closest trigger and action
        # Check for exact match first (including variants)
        for action in self.actions:
            if input_text:
                if input_text == action['trigger'] or input_text in action.get('trigger_variants', []):
                    return action['trigger'], action, 1.0

                if input_text.split(" ")[0] == action['trigger'] or input_text.split(" ")[0] in action.get('trigger_variants', []):
                    return action['trigger'], action, 1.0

        # Prepare all triggers and variants for embedding
        all_triggers = []
        trigger_to_action = {}
        for action in self.actions:
            all_triggers.append(action['trigger'])
            trigger_to_action[action['trigger']] = action
            for variant in action.get('trigger_variants', []):
                all_triggers.append(variant)
                trigger_to_action[variant] = action

        if self.model:
            # Compute embeddings
            input_embedding = self.model.encode([input_text])[0]
            trigger_embeddings = self.model.encode(all_triggers)
            
            # Compute cosine similarities
            similarities = torch.nn.functional.cosine_similarity(
                torch.tensor(input_embedding).unsqueeze(0),
                torch.tensor(trigger_embeddings)
            )
            
            # Adjust similarities based on fuzzy string matching and word order
            for i, trigger in enumerate(all_triggers):
                fuzzy_ratio = fuzz.ratio(input_text.lower(), trigger.lower()) / 100
                word_order_ratio = fuzz.token_sort_ratio(input_text.lower(), trigger.lower()) / 100
                similarities[i] = similarities[i] * 0.5 + fuzzy_ratio * 0.3 + word_order_ratio * 0.2
            
            # Get the index of the highest similarity
            best_match_index = similarities.argmax().item()
            best_match_similarity = similarities[best_match_index].item()
            
            # Return the best match and associated action if it's above the threshold, otherwise return None
            if best_match_similarity >= threshold:
                best_trigger = all_triggers[best_match_index]
                best_action = trigger_to_action[best_trigger]
                return best_trigger, best_action, best_match_similarity
            else:
                return None, None, 0.0
        else:
            return None, None, 0.0

    def check_trigger_words(self, speech, word_type):
        # Get the appropriate words based on the word_type
        if word_type == "stop_speaking":
            trigger_words = self.action.get("stop_speaking", [])
        else:  # "end"
            trigger_words = self.action.get("end", {}).get("words", [])

        # Check for exact match first
        for word in trigger_words:
            if speech.lower() == word.lower():
                return True, 1.0

        # If no exact match, use similarity matching
        all_trigger_words = trigger_words + [word.lower() for word in trigger_words]
        
        # Compute embeddings        
        speech_embedding = self.model.encode([speech])[0]    
        trigger_word_embeddings = self.model.encode(all_trigger_words)
        # Compute cosine similarities
        similarities = torch.nn.functional.cosine_similarity(
            torch.tensor(speech_embedding).unsqueeze(0),
            torch.tensor(trigger_word_embeddings)
        )
        
        # Adjust similarities based on fuzzy string matching and word order
        for i, trigger_word in enumerate(all_trigger_words):
            fuzzy_ratio = fuzz.ratio(speech.lower(), trigger_word.lower()) / 100
            word_order_ratio = fuzz.token_sort_ratio(speech.lower(), trigger_word.lower()) / 100
            similarities[i] = similarities[i] * 0.5 + fuzzy_ratio * 0.3 + word_order_ratio * 0.2
        
        # Get the highest similarity
        best_match_similarity = similarities.max().item()
        
        # Return True if the best match is above a threshold (e.g., 0.8)
        if best_match_similarity >= 0.8:
            return True, best_match_similarity
        else:
            return False, best_match_similarity

    def check_stop_speaking_trigger_words(self, speech):
        return self.check_trigger_words(speech, "stop_speaking")

    def check_end_trigger_words(self, speech):
        return self.check_trigger_words(speech, "end")

    def set_is_transcribing(self, value):
        self.is_transcribing = value

    def set_is_listening(self, value):
        self.is_listening = value

    def set_is_speaking(self, value):
        self.is_speaking = value

    def update_last_speech_time(self):
        value = time.time()
        self.last_speech_time = value

    # THE MEAT STARTS HERE!
    # This is where the speech is processed and the appropriate action is executed

    def process_speech(self, chat_manager, speech):
        print("PROCESS SPEECH")
        
        if not self.is_speaking and not self.is_transcribing:

            signal_manager.voice_update_last_speech_time.emit()
            print("UPDATED LAST SPEECH TIME")
            check_action, action, score = self.find_action(speech)
            if check_action:
                self.previous_action = self.action
                self.action = action

                print(f"Found Action: {score} - (from: {speech})")
                print(f"Action Config:\n{self.action}")

                self.transcription_buffer = [speech]

                signal_manager.voice_set_action.emit(self.action)

                if self.action.get("transcribe", False):
                    self.start_new_transcription()
                else:
                    print("EXECUTE ACTION")
                    self.execute_action(chat_manager, speech)
            else:
                print(f"No action found for: {speech}")
                #todo: we need to try and detect if the user had any other intention
        else:
            if not self.is_speaking:
                self.transcription_buffer.append(speech)           

            # todo: 
            # use check_silence and detect if the sound is coming from the speaker or the user
            # if it is the user, you need to slide the sound volume down,
            # if the user continues to speak, 
            # 
            # stop the speaking, and restart transcription
            if self.is_transcribing:
                # otherwise, Check for end trigger words, and start a new transcription
                is_end, similarity = self.check_stop_speaking_trigger_words(speech)
                if similarity >= 0.6:
                    self.stop_speaking()
                    self.start_new_transcription()



        return speech
      

    def start_new_transcription(self):
        print("EMIT TO VOICE: START TRANSCRIPTION")
        signal_manager.voice_start_transcribing.emit()


    def execute_action(self, chat_manager, speech):       
        print(f"Executing action for speech: {speech}")        
        print(f"Executing action: {self.action.get('trigger', 'Unknown action')}")
        method = self.action.get('method')
        if method:
            module_name, function_name = method.rsplit('.', 1)
            try:
                module = importlib.import_module(f"distr.actions.{module_name}")
                function = getattr(module, function_name)
                function(chat_manager, self.action, {"text": speech, "transcription": self.transcription_buffer})
            except ImportError as e:
                self.logger.error(f"Error importing module {module_name}: {str(e)}")
            except AttributeError as e:
                self.logger.error(f"Error finding function {function_name} in module {module_name}: {str(e)}")
            except Exception as e:
                self.logger.error(f"Error executing action: {str(e)}")
        else:
            self.logger.warning(f"No method specified for action: {speech}")
        return False

    def stop_speaking(self):
        signal_manager.voice_stop_speaking.emit()


    def stop_transcribing(self):
        self.is_transcribing = False
        print("EMIT TO VOICE: STOP TRANSCRIPTION")
        signal_manager.voice_set_transcription_buffer.emit(self.transcription_buffer)
        signal_manager.voice_stop_transcribing.emit()
        print("Action Stop Transcription Executed")

    def cut_transcribing(self):
        self.is_transcribing = False
        print("EMIT TO VOICE: CUT TRANSCRIPTION")
        print("Action Cut Transcription Executed")
        self.transcription_buffer = []
        signal_manager.voice_set_transcription_buffer.emit(self.transcription_buffer)

    def stop(self):
        self.is_running = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.audio:
            self.audio.terminate()
        print("ActionHandler stopped")

