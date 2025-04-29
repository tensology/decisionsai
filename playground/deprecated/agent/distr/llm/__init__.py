from multiprocessing import Process, Queue as MPQueue
from queue import Empty
import time
import os
import ollama
import re
from ..utils import get_timestamp

class LLMEngine:
    def __init__(self, model_name="gemma2:latest", role="You are a friendly assistant named Jarvis", pre_response_callback=None):
        self.input_queue = MPQueue()  # Use multiprocessing Queue
        self.output_queue = MPQueue()  # Use multiprocessing Queue
        self.signal_queue = MPQueue()  # Queue for signaling events
        self.process = None
        self.running = False
        self.buffer = ""  # Buffer for processing text
        
        # Ollama configuration
        self.model_name = model_name
        self.role = role
        self.conversation_history = [{"role": "system", "content": self.role}, {"role": "user", "content": "speak to me like I'm an expert software engineer."}]
        self.generating_reply = False
        self.should_cancel_response = False
        self.stream = True
        self.current_stream = None  # Store the current stream for cancellation
        
        # Initialize Ollama client
        try:
            ollama.chat(model=self.model_name, messages=[{"role": "user", "content": "Hey, introduce yourself. what's your full name?"}])
        except ollama.ResponseError as e:
            print(f'Error: {e.error}')
            if e.status_code == 404:
                print(f"Pulling model: {model_name}")
                ollama.pull(self.model_name)

    def start(self):
        """Start the LLM process"""
        print(f"[{get_timestamp()}] Starting LLM Engine...")
        self.running = True
        self.process = Process(target=self._run)
        self.process.daemon = True
        self.process.start()
        print(f"[{get_timestamp()}] LLM Engine process started with PID: {self.process.pid}")

    def stop(self):
        """Stop the LLM process"""
        print(f"[{get_timestamp()}] Stopping LLM Engine...")
        self.running = False
        if self.process:
            try:
                # Drain queues first
                while not self.input_queue.empty():
                    self.input_queue.get_nowait()
                while not self.output_queue.empty():
                    self.output_queue.get_nowait()
                    
                self.process.terminate()
                self.process.join(timeout=1.0)
                if self.process.is_alive():
                    self.process.kill()
            except Exception as e:
                print(f"[{get_timestamp()}] Error stopping LLM process: {e}")
            finally:
                self.process = None
        print(f"[{get_timestamp()}] LLM Engine stopped")

    def interrupt(self):
        """Interrupt the current response generation"""
        print(f"\n[{get_timestamp()}] ⚠️ LLM received interrupt signal, cancelling current response...")
        self.should_cancel_response = True
        
        # Reset state to allow new responses
        self.generating_reply = False
        self.buffer = ""
        
        # Send signal to clear TTS and playback since we're explicitly interrupting
        self.signal_queue.put({"action": "clear_tts_and_playback"})
        
        # Send a message to the output queue indicating interruption
        try:
            self.output_queue.put({
                "status": "interrupted",
                "message": "Response generation was interrupted by continuous speech"
            })
        except Exception as e:
            print(f"[{get_timestamp()}] Error sending interrupt message to output queue: {e}")

    def extract_sentences(self, text):
        """Extract complete sentences from text using a simpler, more reliable approach"""
        # Buffer the text
        self.buffer += text
        
        # Common abbreviations to handle specially
        abbreviations = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "Rd.", "Ave.", "Blvd.", "Apt."]
        
        # Temporarily replace abbreviations with markers
        protected_buffer = self.buffer
        for i, abbr in enumerate(abbreviations):
            # Use unique markers to avoid collisions
            marker = f"__ABBR{i}__"
            protected_buffer = protected_buffer.replace(abbr, abbr.replace(".", marker))
        
        # Simple end-of-sentence detector that handles typical cases
        result = []
        
        # Look for sentences ending with period, question mark, or exclamation mark
        # followed by space and capital letter or end of text
        matches = list(re.finditer(r'[.!?](?=\s+[A-Z]|\s*$)', protected_buffer))
        
        if matches:
            start_pos = 0
            # Process all complete sentences (all matches except possibly the last one)
            for match in matches:
                end_pos = match.end()
                # Get sentence and restore abbreviations
                sentence = protected_buffer[start_pos:end_pos]
                for i, abbr in enumerate(abbreviations):
                    marker = f"__ABBR{i}__"
                    sentence = sentence.replace(abbr.replace(".", marker), abbr)
                
                result.append(sentence.strip())
                start_pos = end_pos + 1  # +1 to skip the space after the period
            
            # Keep any remaining text in buffer
            self.buffer = protected_buffer[start_pos:].strip()
            
            # Restore any abbreviations in the buffer
            for i, abbr in enumerate(abbreviations):
                marker = f"__ABBR{i}__"
                self.buffer = self.buffer.replace(abbr.replace(".", marker), abbr)
                
            return result
        
        # Handle the case where we have a single complete sentence
        # (ends with terminal punctuation)
        if re.search(r'[.!?]\s*$', protected_buffer):
            sentence = protected_buffer
            # Restore abbreviations
            for i, abbr in enumerate(abbreviations):
                marker = f"__ABBR{i}__"
                sentence = sentence.replace(abbr.replace(".", marker), abbr)
            
            self.buffer = ""
            return [sentence.strip()]
            
        # No complete sentence found
        return []

    def get_llm_response(self, text):
        """Get a response from the LLM and display it"""
        if self.generating_reply:
            print(f"\n[{get_timestamp()}] Already generating a reply, cancelling previous request")
            self.should_cancel_response = True
            
            # Only clear when we're cancelling an existing response
            # to avoid interrupting normal playback flow
            self.signal_queue.put({"action": "clear_tts_and_playback"})
            return
            
        self.should_cancel_response = False
        self.generating_reply = True
        self.buffer = ""  # Clear buffer

        self.signal_queue.put({"action": "clear_tts_and_playback"})        
        print(f"\n[{get_timestamp()}] --- Generating response ---")
        
        try:
            # Add user message to conversation history
            self.conversation_history.append({"role": "user", "content": text})
            
            stream = ollama.chat(
                model=self.model_name,
                messages=self.conversation_history,
                stream=True,
            )
            
            # Store the current stream for potential cancellation
            self.current_stream = stream
            
            # Reset these flags before processing
            self.should_cancel_response = False
            
            # Process the stream
            response_text = ""
            sentences_processed = []
            
            for chunk in stream:
                if self.should_cancel_response:
                    print(f"\n[{get_timestamp()}] --- Response cancelled ---")
                    # Clear the current stream
                    self.current_stream = None
                    break
                
                content = chunk['message']['content'] 
                response_text += content
                
                if self.stream:
                    # Extract complete sentences from the chunk
                    sentences = self.extract_sentences(content)
                    
                    # Process each complete sentence
                    for sentence in sentences:
                        if sentence not in sentences_processed:
                            sentences_processed.append(sentence)
                            self.process_sentence(sentence)
            
            # Process any remaining text in buffer
            if self.buffer.strip() and not self.should_cancel_response:
                if self.buffer not in sentences_processed:
                    sentences_processed.append(self.buffer)
                    self.process_sentence(self.buffer)
                self.buffer = ""
            
            # Only add to history if we got a complete response
            if not self.should_cancel_response and response_text:
                if not self.stream:
                    self.process_sentence(response_text)
                print(f"\n[{get_timestamp()}] --- Response complete ---")
                self.add_assistant_message(response_text)
                
                # Send the complete response to the output queue only once
                self.output_queue.put({
                    "status": "success",
                    "input": text,
                    "response": response_text
                })
            
        except Exception as e:
            print(f"\n[{get_timestamp()}] Error generating response: {e}")
            self.output_queue.put({
                "status": "error",
                "input": text,
                "error": str(e)
            })
        finally:
            self.generating_reply = False
            self.current_stream = None

    def process_sentence(self, sentence):
        """Process a sentence and send it to output queue"""
        sentence = sentence.strip()
        if not sentence:
            return        

        # Clean up the text - remove special characters, emojis, etc.
        text = sentence.encode('ascii', 'ignore').decode('ascii')
        
        # Handle basic markdown formatting more gracefully
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'`(.*?)`', r'\1', text)        # Code
        text = re.sub(r'~~(.*?)~~', r'\1', text)      # Strikethrough
        
        # Remove horizontal rules
        text = re.sub(r'---+', '', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # Skip if the text is empty after cleanup
        if not text:
            return
        
        # Print the cleaned sentence with timestamp
        print(f"[{get_timestamp()}] {text}")
        
        # Send each processed sentence to the output queue with special status
        # so it can be immediately processed by TTS
        self.output_queue.put({
            "status": "sentence",
            "text": text
        })

    def add_assistant_message(self, message):
        """Add an assistant message to the conversation history"""
        self.conversation_history.append({"role": "assistant", "content": message})

    def _run(self):
        """Main process loop for LLM"""
        print(f"[{get_timestamp()}] LLM Engine process started")
        print(f"[{get_timestamp()}] LLM process running with PID: {os.getpid()}")
        
        # We no longer need to test the queue since that test message was causing issues
        # with the initial greeting not being sent to TTS
        
        while self.running:
            try:
                # Check for new input with a short timeout
                try:
                    # Try to get text from the queue
                    try:
                        text = self.input_queue.get(timeout=0.1)
                        # print(f"\n[{get_timestamp()}] 🤖 Processing Text: {text}")
                        
                        # Process with Ollama
                        self.get_llm_response(text)
                    except Empty:
                        continue
                    except Exception as e:
                        print(f"[{get_timestamp()}] Error getting text from queue: {e}")
                        time.sleep(0.1)
                except Exception as e:
                    print(f"[{get_timestamp()}] Error in queue processing: {e}")
                    time.sleep(0.1)
                    
            except Exception as e:
                print(f"[{get_timestamp()}] Error in LLM process: {e}")
                time.sleep(0.1)
                
        print(f"[{get_timestamp()}] LLM Engine process stopped")

    def process_text(self, text):
        """Process text and get LLM response"""
        if not text or not text.strip():
            print(f"[{get_timestamp()}] Empty text received, ignoring")
            return False
        
        if text.lower() in [
            "(clears throat)",
            "[blank audio]",
            "[no audio]", 
            "[clapping]", 
            "(clapping)", 
            "[laughter]", 
            "[laugh]", 
            "(laughter)", 
            "(laugh)", 
            "[music]", 
            "(music)", 
            "[bleep]", 
            "(bleep)", 
            "[beep]", 
            "(beep)", 
            "[bell]", 
            "(bell)", 
            "[static]", 
            "[popping]", 
            "(popping)", 
            "[silence]", 
            "(silence)", 
            "[sigh]",
            "(sighs)",
            "[sighing]", 
            "(sighing)", 
            "[applause]", 
            "(applause)", 
            "(bell ringing)",
            "(clicking)",
            "(coughing)",
            "(knocking)",
            "[coughing]",
            "[tapping]",
            "(beatboxing)",
            "(tapping)",
            "[dog barks]",
            "(cough)",
            "(breathing heavily)"]:
            return False

        # We've removed the automatic clear signal to prevent interrupting ongoing TTS playback
        
        if self.running and self.process and self.process.is_alive():
            try:
                print(f"\n[{get_timestamp()}] 📥 LLM Received Text: {text}")
                # print(f"[{get_timestamp()}] LLM process alive: {self.process.is_alive()}, PID: {self.process.pid}")
                
                # Try to put the text in the queue
                try:
                    self.input_queue.put_nowait(text)
                    # print(f"[{get_timestamp()}] Text successfully sent to LLM queue")
                    return True
                except Exception as e:
                    # print(f"[{get_timestamp()}] Error putting text in queue: {e}")
                    return False
            except Exception as e:
                # print(f"[{get_timestamp()}] Error in process_text: {e}")
                return False
        else:
            # print(f"[{get_timestamp()}] LLM not ready: running={self.running}, process={self.process}, alive={self.process.is_alive() if self.process else False}")
            return False

    def get_result(self):
        """Get processed results from LLM"""
        if self.running and self.process and self.process.is_alive():
            try:
                if not self.output_queue.empty():
                    result = self.output_queue.get_nowait()
                    # Don't print the final response here, it's already been printed sentence by sentence
                    return result
            except Exception as e:
                print(f"[{get_timestamp()}] Error getting LLM result: {e}")
        return None

    def send_welcome_message(self):
        """Send a welcome message to the user with guaranteed delivery of all sentences"""
        try:
            # Get the assistant name from the role
            assistant_name = self.role.split('named ')[1].split('.')[0] if 'named ' in self.role else 'your assistant'
            
            # Welcome message with a few different sentences to demonstrate the streaming
            welcome_sentences = [
                f"Hello! I'm {assistant_name}.",
                "I'm here to help you with your questions.",
                "What can I assist you with today?"
            ]
                        
            # Process each sentence directly and add a small delay to ensure TTS has time to process
            for i, sentence in enumerate(welcome_sentences):
                self.process_sentence(sentence)
                # Add a small delay between sentences to ensure proper TTS processing
                time.sleep(0.1)
                
            # Add to conversation history
            full_message = " ".join(welcome_sentences)
            self.add_assistant_message(full_message)            
            return full_message
            
        except Exception as e:
            print(f"[{get_timestamp()}] Error sending welcome message: {e}")
            return None
