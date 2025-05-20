"""
LLM.py - Language Model Interface and Management System

This module provides a robust language model interface with features including:
- Multi-process text processing
- Streaming sentence-by-sentence output
- Conversation history management
- Interruption handling
- Error recovery

The system uses Ollama for model inference, with support for
different models and conversation contexts.

Key Features:
- Process-based LLM execution for responsiveness
- Streaming output with sentence-level parsing
- Queue-based communication between processes
- TTS integration for spoken responses
- Interruption and cancellation support

Class Organization:
1. Initialization and Setup
2. Process Management
3. Text Processing and Sentence Extraction
4. Response Generation
5. Conversation Management
"""

from multiprocessing import Process, Queue as MPQueue
from queue import Empty
import logging
import ollama
import time
import uuid
import os
import re

# Import utilities for sentence processing and timestamp generation
from .sentences import extract_sentences
from .utils import get_timestamp, TextProcessor


class LLMEngine:
    """
    Language model interface with advanced features including streaming output,
    sentence extraction, and TTS integration.
    
    This class manages the language model interaction with support for:
    - Multi-process execution
    - Streaming output with sentence-level granularity
    - Conversation history management
    - TTS integration
    - Response interruption and cancellation
    - Error handling and recovery
    """
    
    # ===========================================
    # 1. Initialization and Setup
    # ===========================================
    def __init__(self, agent_name=None, role=None, engine="ollama", api_key=None, model_name="gemma3:4b"):
        """
        Initialize the LLM engine with model and communication settings.
        
        Args:
            agent_name (str): Name of the agent for conversation context
            role (str, optional): System role prompt for the conversation
            engine (str): Name of the LLM engine to use (currently only "ollama" supported)
            api_key (str, optional): API key for the LLM service if required
            model_name (str): Name of the model to use for inference
        """
        
        # Initialize logger
        self.logger = logging.getLogger(__name__)
        
        # Initialize communication queues
        self.input_queue = MPQueue()    # Queue for incoming text to process
        self.output_queue = MPQueue()   # Queue for processed responses
        self.signal_queue = MPQueue()   # Queue for control signals between processes
        self.tts_clear_ack_queue = MPQueue()  # Ack queue for TTS/playback clear
        
        # Initialize process control
        self.process = None
        self.running = False
        
        # Initialize text processing
        self.buffer = ""                # Buffer for accumulating text chunks
        self.agent_name = agent_name
        self.tts_queue = None           # Queue for sending text to TTS (will be set later)
        
        # Set up role/system prompt
        if role is None:
            self.role = "You are an AI assistant."
        else:
            self.role = role

        # Initialize model configuration
        self.model_name = model_name

        # Initialize conversation history with system prompts
        self.conversation_history = [
            {"role": "system", "content": f"Your name is {agent_name}."},
            {"role": "system", "content": self.role},
            {"role": "system", "content": f"Our current date and time is {get_timestamp()}, it is a {time.strftime('%A')}, and our timezone is {time.strftime('%Z')}"},
            {"role": "system", "content": "Don't voice out actions (ie. 'leans back and raises eyebrows')."},
            {"role": "system", "content": "Your response is being sent to a TTS engine, so don't include any special characters or markdown. Use sentences, not lists."},
            {"role": "system", "content": "Don't list numbers."},
            {"role": "system", "content": "Never write out a full domain name."},
            {"role": "system", "content": "Don't deviate from my questions."},
            {"role": "system", "content": "Do not invent or use names with titles or honorifics (such as “Mr.”, “Mrs.”, “Dr.”, etc.)."},
            {"role": "system", "content": "You were built by 'Paul Hoft' from Tensology Technologies as an opensource project to show the power of Advanced Voice."},
            {"role": "system", "content": "speak to me intelligently."},
        ]
        
        # Initialize response state tracking
        self.generating_reply = False
        self.should_cancel_response = False
        self.stream = True
        self.current_stream = None
        
        # Initialize Ollama client and pull model if needed
        self._initialize_model()
    
    def _initialize_model(self):
        """
        Initialize the LLM model and pull it if not available.
        Handles model availability checking and download.
        """
        try:
            # Quick test to check if model is available
            ollama.chat(
                model=self.model_name, 
                messages=[{"role": "user", "content": "Hey, introduce yourself. what's your full name?"}]
            )
            self.logger.info(f"Model {self.model_name} initialized successfully")
        except ollama.ResponseError as e:
            self.logger.error(f"Error initializing model: {e.error}")
            # If model not found, pull it
            if e.status_code == 404:
                self.logger.info(f"Pulling model: {self.model_name}")
                ollama.pull(self.model_name)
                self.logger.info(f"Model {self.model_name} pulled successfully")
        except Exception as e:
            self.logger.error(f"Unexpected error initializing model: {e}")

    # ===========================================
    # 2. Process Management
    # ===========================================
    def start(self):
        """
        Start the LLM process for handling requests.
        Creates a separate process for LLM execution to maintain responsiveness.
        """
        self.logger.info(f"Starting LLM Engine...")
        self.running = True
        
        # Create and start the process
        self.process = Process(target=self._run)
        self.process.daemon = True
        self.process.start()
        
        self.logger.info(f"LLM Engine process started with PID: {self.process.pid}")

    def stop(self):
        """
        Stop the LLM process and clean up resources.
        Ensures proper termination of the process and release of resources.
        """
        self.logger.info(f"Stopping LLM Engine...")
        self.running = False
        
        if self.process:
            try:
                # Clean up step 1: Drain all queues to prevent blocking
                for queue in [self.input_queue, self.output_queue, self.signal_queue]:
                    try:
                        while not queue.empty():
                            queue.get_nowait()
                    except Exception as e:
                        self.logger.error(f"Error draining queue: {e}")
                    
                # Clean up step 2: Terminate process with timeout
                self.process.terminate()
                self.process.join(timeout=1.0)
                
                # Clean up step 3: Force kill if still running
                if self.process.is_alive():
                    self.logger.warning("Process did not terminate gracefully, forcing kill")
                    self.process.kill()
                    
                # Clean up step 4: Release queue resources
                for queue in [self.input_queue, self.output_queue, self.signal_queue]:
                    try:
                        queue.close()
                        queue.join_thread()
                    except Exception as e:
                        self.logger.error(f"Error closing queue: {e}")
                    
            except Exception as e:
                self.logger.error(f"Error stopping LLM process: {e}")
            finally:
                self.process = None
                
        self.logger.info(f"LLM Engine stopped")

    def _run(self):
        """
        Main process loop for LLM execution.
        Runs in a separate process to handle requests asynchronously.
        """
        self.logger.info(f"LLM Engine process started with PID: {os.getpid()}")
        
        while self.running:
            try:
                # Try to get text from the input queue with timeout
                try:
                    text = self.input_queue.get(timeout=0.1)
                    self.logger.info(f"📥 LLM Received Text: {text}")
                    
                    # Process the input with LLM
                    self.get_llm_response(text)
                except Empty:
                    # No input available, continue loop
                    continue
                except Exception as e:
                    self.logger.error(f"Error getting text from queue: {e}")
                    time.sleep(0.1)  # Brief pause to prevent tight looping
            except Exception as e:
                self.logger.error(f"Error in LLM process main loop: {e}")
                time.sleep(0.1)  # Brief pause to prevent tight looping
                
        self.logger.info(f"LLM Engine process stopped")

    def interrupt(self):
        """
        Interrupt the current response generation.
        Sends a signal to stop the current response and reset state.
        """
        self.logger.info(f"⚠️ LLM received interrupt signal, cancelling current response...")
        self.should_cancel_response = True
        
        # Reset state to allow new responses
        self.generating_reply = False
        self.buffer = ""
        
        # Clear TTS and playback first
        try:
            # Get the session object to access TTS and playback
            if hasattr(self.llm_callback, '__self__'):
                session = self.llm_callback.__self__
                if hasattr(session, 'clear_tts_and_playback'):
                    session.clear_tts_and_playback()
                    self.logger.info("Cleared TTS and playback")
        except Exception as e:
            self.logger.error(f"Error clearing TTS and playback: {e}")
        
        # Send signal to clear TTS and playback
        self.signal_queue.put({"action": "clear_tts_and_playback"})
        
        # Notify about interruption through output queue
        try:
            self.output_queue.put({
                "status": "interrupted",
                "message": "Response generation was interrupted by continuous speech"
            })
        except Exception as e:
            self.logger.error(f"Error sending interrupt message to output queue: {e}")

    # ===========================================
    # 3. Text Processing and Sentence Extraction
    # ===========================================
    def extract_sentences(self, text):
        """
        Extract sentences from the LLM text output stream.
        Delegates all sentence processing to the sentences.py module.
        
        Args:
            text (str): Text chunk received from LLM response stream
            
        Returns:
            list: List of complete sentences extracted
        """
        # Use extract_sentences from sentences.py to handle processing
        sentences, self.buffer = extract_sentences(text, self.buffer)
        return sentences

    def process_sentence(self, sentence, sentence_id=None, group_id=None, position=None):
        """Process a sentence and send it to output queue and TTS with metadata."""
        if not sentence or not isinstance(sentence, str):
            return
        # Clean the text using TextProcessor
        text = TextProcessor.clean_sentence_for_tts(sentence)
        # Skip if the text is empty after cleanup
        if not text:
            return
        # Print the cleaned sentence with timestamp
        print(f"[{get_timestamp()}] {text}")
        self.logger.info(text)
        # Store this sentence as the last processed one
        self.last_processed_sentence = text
        try:
            # Send processed sentence to output queue
            self.output_queue.put({
                "status": "sentence",
                "text": text
            })
            # Also send to TTS queue if available
            if self.tts_queue:
                self.logger.info(f"[{get_timestamp()}] 📤 LLM sending to TTS queue: '{text}'")
                # Send with metadata for ordering and grouping
                self.tts_queue.put({
                    "text": text,
                    "sentence_id": sentence_id or str(uuid.uuid4()),
                    "group_id": group_id,
                    "position": position,
                    "status": "sentence",
                    "timestamp": time.time()
                })
            else:
                self.logger.warning(f"[{get_timestamp()}] ❌ TTS queue not connected, cannot send text: '{text}'")
        except Exception as e:
            self.logger.error(f"Error sending sentence to output queue: {e}")
            # Try to reconnect TTS queue if it's None
            if not self.tts_queue:
                self.logger.warning("TTS queue is None, attempting to reconnect...")
                # You might want to add reconnection logic here if needed

    def process_text(self, text):
        """
        Process input text and send to LLM for response.
        Performs initial filtering and queues valid text for processing.
        
        Args:
            text (str): Input text to process
            
        Returns:
            bool: True if text was accepted for processing, False otherwise
        """
        # Initialize last_sentences list if not exists
        if not hasattr(self, 'last_sentences'):
            self.last_sentences = []
            
        # Skip empty text
        if not text or not text.strip():
            self.logger.info(f"Empty text received, ignoring")
            return False
        
        # Skip sound descriptions, audio artifacts, etc.
        if TextProcessor.is_audio_artifact(text):
            self.logger.info(f"Filtered out audio artifact: '{text}'")
            return False

        # Store this sentence for context in future processing
        self.last_sentences.append(text)
        # Keep only the last 5 sentences for context
        if len(self.last_sentences) > 5:
            self.last_sentences = self.last_sentences[-5:]

        # Check if LLM process is ready
        if self.running and self.process and self.process.is_alive():
            try:
                self.logger.info(f"📥 LLM Received Text: {text}")
                
                # Send text to LLM process through input queue
                try:
                    self.input_queue.put_nowait(text)
                    return True
                except Exception as e:
                    self.logger.error(f"Error putting text in queue: {e}")
                    return False
            except Exception as e:
                self.logger.error(f"Error in process_text: {e}")
                return False
        else:
            self.logger.warning(f"LLM not ready: running={self.running}, process={self.process}, alive={self.process.is_alive() if self.process else False}")
            return False

    # ===========================================
    # 4. Response Generation
    # ===========================================
    def get_llm_response(self, text):
        """
        Get a streaming response from the LLM model.
        Manages conversation flow, streaming, sentence extraction, and error handling.
        
        Args:
            text (str): User text to process
        """
        # Check if already generating a response
        if self.generating_reply:
            print(f"\n[{get_timestamp()}] Already generating a reply, cancelling previous request")
            self.logger.info(f"Already generating a reply, cancelling previous request")
            self.should_cancel_response = True
            # Clear TTS and playback when cancelling
            self.signal_queue.put({"action": "clear_tts_and_playback"})
            return
        # Set up for new response
        self.should_cancel_response = False
        self.generating_reply = True
        self.buffer = ""  # Clear sentence buffer
        # Signal to clear TTS and playback queue
        self.signal_queue.put({"action": "clear_tts_and_playback"})        
        # Wait for AgentSession to acknowledge TTS/playback clear
        try:
            self.tts_clear_ack_queue.get(timeout=2.0)
        except Exception:
            self.logger.warning("Timeout waiting for TTS/playback clear ack")
        print(f"\n[{get_timestamp()}] --- Generating response ---")
        self.logger.info(f"--- Generating response ---")
        try:
            # Add user message to conversation history
            self.conversation_history.append({"role": "user", "content": text})
            # Request streaming response from Ollama
            stream = ollama.chat(
                model=self.model_name,
                messages=self.conversation_history,
                stream=True,
            )
            # Store stream reference for potential cancellation
            self.current_stream = stream
            # Process streaming response
            response_text = ""
            group_id = str(uuid.uuid4())
            position_counter = 0
            sent_sentences = set()  # Track (sentence, group_id) sent to TTS
            for chunk in stream:
                # Check for cancellation request
                if self.should_cancel_response:
                    print(f"\n[{get_timestamp()}] --- Response cancelled ---")
                    self.logger.info(f"--- Response cancelled ---")
                    self.current_stream = None
                    break
                # Extract content from chunk
                content = chunk['message']['content'] 
                response_text += content
                # Process streaming response sentence by sentence
                if self.stream:
                    # Extract complete sentences from the chunk
                    sentences = self.extract_sentences(content)
                    # Process each complete sentence
                    for sentence in sentences:
                        key = (sentence.strip(), group_id)
                        if key not in sent_sentences:
                            sent_sentences.add(key)
                            sentence_id = str(uuid.uuid4())
                            self.process_sentence(
                                sentence,
                                sentence_id=sentence_id,
                                group_id=group_id,
                                position=position_counter
                            )
                            time.sleep(0.5)  # Robust delay for TTS to process
                            position_counter += 1
            # Process any remaining text in buffer
            if self.buffer.strip() and not self.should_cancel_response:
                key = (self.buffer.strip(), group_id)
                if key not in sent_sentences:
                    sent_sentences.add(key)
                    sentence_id = str(uuid.uuid4())
                    self.process_sentence(
                        self.buffer,
                        sentence_id=sentence_id,
                        group_id=group_id,
                        position=position_counter
                    )
                    self.buffer = ""
            # Handle response completion
            if not self.should_cancel_response and response_text:
                # For non-streaming mode, process entire response at once
                if not self.stream:
                    sentence_id = str(uuid.uuid4())
                    self.process_sentence(
                        response_text,
                        sentence_id=sentence_id,
                        group_id=group_id,
                        position=0
                    )
                # Apply post-processing to the full response
                clean_response = TextProcessor.clean_text(response_text)
                print(f"\n[{get_timestamp()}] --- Response complete ---")
                self.logger.info(f"--- Response complete ---")
                # Add assistant response to conversation history
                self.add_assistant_message(clean_response)
                # Send complete response to output queue
                self.output_queue.put({
                    "status": "success",
                    "input": text,
                    "response": clean_response
                })
        except Exception as e:
            print(f"\n[{get_timestamp()}] Error generating response: {e}")
            self.logger.error(f"Error generating response: {e}")
            # Report error through output queue
            self.output_queue.put({
                "status": "error",
                "input": text,
                "error": str(e)
            })
        finally:
            # Reset state regardless of success or failure
            self.generating_reply = False
            self.current_stream = None

    def get_result(self):
        """
        Get processed results from LLM output queue.
        Retrieves any available output without blocking.
        
        Returns:
            dict: Result data if available, None otherwise
        """
        if self.running and self.process and self.process.is_alive():
            try:
                if not self.output_queue.empty():
                    result = self.output_queue.get_nowait()
                    return result
            except Exception as e:
                self.logger.error(f"Error getting LLM result: {e}")
        return None

    # ===========================================
    # 5. Conversation Management
    # ===========================================
    def add_assistant_message(self, message):
        """
        Add an assistant message to the conversation history.
        
        Args:
            message (str): Message content to add
        """
        self.conversation_history.append({"role": "assistant", "content": message})

    def send_welcome_message(self):
        """
        Send a welcome message to the user.
        Generates a predefined welcome message with controlled delivery
        for proper TTS processing.
        
        Returns:
            str: Full welcome message if successful, None otherwise
        """
        try:            
            # Welcome message with multiple sentences to demonstrate streaming
            welcome_sentences = [
                f"Hello! I'm {self.agent_name}.",
                "I'm here to help you with your questions.",
                "What can I assist you with today?"
            ]
            import time
            # Process each sentence with delay to ensure TTS processing
            for sentence in welcome_sentences:
                self.process_sentence(sentence)
                time.sleep(1.0)  # Robust delay for TTS to process
            # Combine sentences and add to conversation history
            full_message = " ".join(welcome_sentences)
            self.add_assistant_message(full_message)            
            return full_message
        except Exception as e:
            self.logger.error(f"Error sending welcome message: {e}")
            return None

    def set_tts_queue(self, tts_queue):
        """
        Set the TTS queue for sending text to the TTS engine.
        
        Args:
            tts_queue (Queue): Queue for sending text to TTS
        """
        self.tts_queue = tts_queue
        self.logger.info(f"TTS queue connected to LLM engine")
