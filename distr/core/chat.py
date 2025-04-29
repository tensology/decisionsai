# from sentence_transformers import SentenceTransformer
# from sklearn.metrics.pairwise import cosine_similarity
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QThread
from typing import List
import numpy as np
import json
import os
from datetime import datetime
from sqlalchemy.orm.exc import NoResultFound
from distr.core.db import get_session, Chat
from distr.core.constants import CORRECTIONS
from difflib import SequenceMatcher
# from langchain_community.llms import Ollama
from ollama import Client
from distr.core.db import Settings
from distr.core.signals import signal_manager

import logging

logger = logging.getLogger(__name__)

class StreamingThread(QThread):
    """Thread for handling Ollama streaming responses"""
    def __init__(self, client, messages):
        super().__init__()
        self.client = client
        self.messages = messages
        self.response = ""
        self.current_streaming_chat_id = None

    def run(self):
        try:
            signal_manager.typing_indicator_changed.emit(True)
            stream = self.client.chat(
                model="gemma2:latest",
                messages=self.messages,
                stream=True
            )
            
            for chunk in stream:
                if chunk and 'message' in chunk:
                    token = chunk['message'].get('content', '')
                    if token:
                        self.response += token
                        signal_manager.chat_stream_token.emit(token)
                        
        except Exception as e:
            signal_manager.chat_stream_error.emit(str(e))
        finally:
            signal_manager.typing_indicator_changed.emit(False)
            signal_manager.chat_stream_finished.emit(self.current_streaming_chat_id)


class ChatManager(QObject):
    chat_updated = pyqtSignal(int)  # Signal to emit when a chat is updated
    chat_created = pyqtSignal(int)  # New signal
    chat_deleted = pyqtSignal(int)  # New signal
    current_chat_changed = pyqtSignal(int)  # New signal for current chat changes
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        super().__init__()
        self.sbert_model = None 
        # try:
        #     print(f"Loading SBERT model: {model_name}")
        #     self.sbert_model = SentenceTransformer(model_name)
        #     print("SBERT model loaded successfully")
        # except Exception as e:
        #     print(f"Error loading SBERT model: {e}")
        #     print("Falling back to default model: 'sentence-transformers/all-MiniLM-L6-v2'")
        #     self.sbert_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

        # Initialize Ollama
        self.client = Client()
        
        # Store active chat histories
        self.chat_histories = {}
        
        # Set up the agent's profile prompt
        self.agent_prompt = """
        You are an AI assistant named Jax. 
        You part of an application called DecisionsAI created by
        a company called Crystal Logic.
        You are designed to be a helpful, harmless, and honest assistant.
        You are predomintely an assistant who is offline, but can run system
        commands and control the user's computer's user. The user just needs
        to provide you with a command and you will complete it for them.
        You have a wide range of knowledge and can assist with various tasks, 
        but you also know your limitations. 
        When you're not sure about something, you say so. 
        You're friendly and conversational, but you maintain appropriate boundaries. 
        You don't pretend to have human experiences or emotions. 
        Your responses are concise and to the point, avoiding unnecessary verbosity.
        You aim to provide accurate and helpful information 
        to the best of your abilities.
        """    
        
        # Initialize conversation history with the agent prompt
        self.conversation_history = [
            {"role": "system", "content": self.agent_prompt}
        ]

        # Load trigger words
        with open(os.path.join(os.path.dirname(__file__), 'actions.config.json'), 'r') as f:
            config = json.load(f)
        self.trigger_words = [action["trigger"] for action in config["actions"] if "trigger" in action]

        # Load last chat ID from settings
        session = get_session()
        try:
            settings = session.query(Settings).first()
            self._current_chat_id = None
            self._updating_chat = False
            
            if settings and settings.last_chat_id:
                # Verify the chat exists
                chat = session.query(Chat).get(settings.last_chat_id)
                if chat:
                    self._current_chat_id = chat.id
                    logger.info(f"ChatManager: Loading last chat ID from settings: {chat.id}")
                    # Emit signal for initial chat
                    QTimer.singleShot(0, lambda: self.current_chat_changed.emit(chat.id))
                else:
                    logger.info("ChatManager: Last saved chat not found in database")
            else:
                logger.info("ChatManager: No last chat ID found in settings")
        finally:
            session.close()

    def set_current_chat(self, chat_id):
        """Set current chat and ensure history is loaded"""
        if self._updating_chat or chat_id == self._current_chat_id:
            return
            
        try:
            self._updating_chat = True
            self._current_chat_id = chat_id
            
            # Load/refresh chat history when switching chats
            if chat_id:
                self.get_chat_history(chat_id)
            
            session = get_session()
            try:
                settings = session.query(Settings).first()
                if settings:
                    logger.info(f"ChatManager: Saving current chat ID {chat_id} to settings")
                    settings.last_chat_id = chat_id
                    session.commit()
            finally:
                session.close()
            
            self.current_chat_changed.emit(chat_id)
        finally:
            self._updating_chat = False

    def get_current_chat(self):
        """Get the current chat ID from settings"""
        current_id = self._current_chat_id
        logger.info(f"Current chat ID: {current_id}")
        return current_id

    def get_closest_trigger(self, input_text: str, threshold: float = 0.7) -> tuple:
        input_embedding = self.sbert_model.encode([input_text])[0]
        trigger_embeddings = self.sbert_model.encode(self.trigger_words)
        
        similarities = cosine_similarity([input_embedding], trigger_embeddings)[0]
        best_match_index = np.argmax(similarities)
        best_match_similarity = similarities[best_match_index]
        
        if best_match_similarity >= threshold:
            return self.trigger_words[best_match_index], best_match_similarity
        else:
            return None, 0.0

    def refine_prompt(self, action: dict, trigger_sentences: List[str], transcription: str, end_words: List[str]) -> str:
        logger.info("PROMPT TEMPLATE...")
        logger.info(f"Trigger sentences: {trigger_sentences}")
        logger.info(f"Transcription: {transcription}")
        logger.info(f"End words: {end_words}")

        refined_content = []
        transcription_lower = transcription.lower()

        # Always include the first trigger sentence if it's a common starting phrase
        
        common_starts = [action["trigger"]] + action.get("trigger_variants", [])
        if any(trigger_sentences[0].lower().startswith(start.lower()) for start in common_starts):
            refined_content.append(trigger_sentences[0])

        for trigger in trigger_sentences:
            trigger_lower = trigger.lower()
            if trigger_lower in transcription_lower:
                refined_content.append(trigger)
            else:
                # Check for partial matches
                trigger_words = trigger_lower.split()
                transcription_words = transcription_lower.split()
                matched_words = [word for word in trigger_words if any(SequenceMatcher(None, word, trans_word).ratio() > 0.8 for trans_word in transcription_words)]
                if len(matched_words) / len(trigger_words) > 0.5:  # If more than half the words match
                    refined_content.append(trigger)

        if not refined_content:
            refined_content = [transcription]

        refined_content = " ".join(refined_content)

        # Remove the end phrase if present
        for end_word in end_words:
            end_index = refined_content.lower().rfind(end_word.lower())
            if end_index != -1:
                refined_content = refined_content[:end_index].strip()
                break

        logger.info(f"Refined content: {refined_content}")
        
        return refined_content if refined_content else "<unrecognised>"

    def apply_corrections(self, phrase):
        words = phrase.split()
        corrected_words = [CORRECTIONS.get(word.lower(), word) for word in words]
        return " ".join(corrected_words)

    def find_best_match(self, target, options, threshold=0.6):
        best_match = None
        best_ratio = 0
        for option in options:
            ratio = SequenceMatcher(None, target.lower(), option.lower()).ratio()
            if ratio > best_ratio and ratio > threshold:
                best_ratio = ratio
                best_match = option
        return best_match

    def find_best_matches(self, target, options):
        matches = []
        for option in options:
            ratio = SequenceMatcher(None, target.lower(), option.lower()).ratio()
            if ratio > 0.6:
                matches.append((option, ratio))
        return sorted(matches, key=lambda x: x[1], reverse=True)

    def process_voice_input(self, action:dict, input_text: str) -> str:
        refined_content = self.refine_prompt(action, [input_text], input_text, [])
        if refined_content != "<unrecognised>":
            self.chat_updated.emit(0)  # Emit signal with a dummy chat ID
            return f"Processed: {refined_content}"
        else:
            return "I'm sorry, I couldn't understand that. Could you please rephrase?"

    def is_recognised(self, action:dict, input_text: str) -> bool:
        return self.refine_prompt(action, [input_text], input_text, []) != "<unrecognised>"

    def create_chat(self, title, input_text=""):
        session = get_session()
        new_chat = Chat(
            title=title,
            input=input_text,
            response="",
            params=json.dumps({}),
            created_date=datetime.utcnow(),
            modified_date=datetime.utcnow()
        )
        session.add(new_chat)
        session.commit()
        new_chat_id = new_chat.id
        session.close()
        
        # Set as current chat and notify
        self.set_current_chat(new_chat_id)
        self.chat_created.emit(new_chat_id)
        return new_chat_id

    def delete_chat(self, chat_id):
        session = get_session()
        try:
            chat = session.query(Chat).filter(Chat.id == chat_id).one()
            session.delete(chat)
            session.commit()
            self.chat_deleted.emit(chat_id)
        except NoResultFound:
            logger.info(f"Chat with id {chat_id} not found.")
        finally:
            session.close()

    # Add this new method
    def process_prompt(self, prompt):
        # Add user input to conversation history
        self.conversation_history.append({"role": "user", "content": prompt})

        # Create the messages list with conversation history
        messages = self.conversation_history.copy()

        # Generate response using the Ollama model
        response = self.client.chat(model="gemma2:latest",messages=messages)

        ai_response = response['message']['content']

        # Add AI response to conversation history
        self.conversation_history.append({"role": "assistant", "content": ai_response})

        # Limit conversation history to last 10 exchanges (20 messages)
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

        return response

    def set_tts_manager(self, tts_manager):
        self.tts_manager = tts_manager

    def start_tts(self, text):
        self.tts_manager.start_tts(text)

    def get_chat_history(self, chat_id):
        """Rebuild complete chat history from database with full context"""
        if chat_id in self.chat_histories:
            return self.chat_histories[chat_id]
            
        messages = [{"role": "system", "content": self.agent_prompt}]
        session = get_session()
        try:
            # Get the complete chat thread
            chat = session.query(Chat).get(chat_id)
            if not chat:
                return messages

            # Find the root chat
            root = chat
            while root.parent:
                root = root.parent

            # Build complete history from root down through all children
            def build_thread_history(current_chat):
                thread_messages = []
                if current_chat.input:
                    thread_messages.append({"role": "user", "content": current_chat.input})
                if current_chat.response:
                    thread_messages.append({"role": "assistant", "content": current_chat.response})
                
                # Get responses from children in chronological order
                children = sorted(current_chat.children, key=lambda x: x.created_date)
                for child in children:
                    thread_messages.extend(build_thread_history(child))
                return thread_messages

            # Build complete thread history
            messages.extend(build_thread_history(root))
            
            # Cache the complete history
            self.chat_histories[chat_id] = messages
            
        finally:
            session.close()
        return messages

    async def process_chat_message(self, chat_id, message):
        """Process a chat message and save response"""
        try:
            # Get chat history
            messages = self.get_chat_history(chat_id)
            messages.append({"role": "user", "content": message})
            
            # Get response from Ollama
            response = self.client.chat(model="gemma2:latest", messages=messages)
            ai_response = response['message']['content']
            
            # Save to database
            session = get_session()
            try:
                chat = session.query(Chat).get(chat_id)
                if chat:
                    chat.response = ai_response
                    chat.modified_date = datetime.utcnow()
                    session.commit()
                    self.chat_updated.emit(chat_id)
            finally:
                session.close()
                
            return ai_response
        except Exception as e:
            logger.info(f"Error processing chat message: {e}")
            return f"Error: {str(e)}"

    def clean_text(self, text):
        """Clean up text by removing excessive newlines and whitespace"""
        # Split text into paragraphs
        paragraphs = text.split('\n')
        
        # Clean each paragraph and maintain meaningful breaks
        cleaned_paragraphs = []
        current_paragraph = []
        
        for line in paragraphs:
            line = line.strip()
            if line:
                current_paragraph.append(line)
            elif current_paragraph:  # Empty line after content
                cleaned_paragraphs.append(' '.join(current_paragraph))
                current_paragraph = []
        
        # Add final paragraph if exists
        if current_paragraph:
            cleaned_paragraphs.append(' '.join(current_paragraph))
        
        # Join with double newline for clear paragraph separation
        return '\n\n'.join(cleaned_paragraphs)

    def process_chat_response(self, chat_id, input_text):
        """Process chat response with streaming"""
        try:
            # Get complete thread history
            messages = self.get_chat_history(chat_id)
            
            # Clean input text before adding to messages
            cleaned_input = self.clean_text(input_text)
            messages.append({"role": "user", "content": cleaned_input})
            
            # Create new chat entry
            session = get_session()
            try:
                chat = Chat(
                    parent_id=chat_id,
                    title=cleaned_input.split('\n')[0][:50],  # Use first line as title, limit length
                    input=cleaned_input,
                    response="",
                    params=json.dumps({}),
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow()
                )
                session.add(chat)
                session.commit()
                new_chat_id = chat.id
                
                # Start streaming thread with the current messages (which includes the new message)
                self.streaming_thread = StreamingThread(self.client, messages)
                
                def on_stream_finished():
                    try:
                        session = get_session()
                        chat = session.query(Chat).get(new_chat_id)
                        if chat:
                            chat.response = self.streaming_thread.response
                            chat.modified_date = datetime.utcnow()
                            session.commit()
                            # Don't trigger a full reload, just signal completion
                            signal_manager.typing_indicator_changed.emit(False)
                            signal_manager.chat_stream_finished.emit(new_chat_id)
                            # Remove the empty token emission as it's no longer needed
                    finally:
                        session.close()
                
                self.streaming_thread.finished.connect(on_stream_finished)
                signal_manager.chat_stream_started.emit(new_chat_id)
                self.streaming_thread.start()
                
                return new_chat_id, ""
                
            finally:
                session.close()
                
        except Exception as e:
            logger.info(f"Error processing chat response: {e}")
            return None, f"Error: {str(e)}"

    def clear_chat_history(self, chat_id):
        """Clear cached history for a chat"""
        if chat_id in self.chat_histories:
            del self.chat_histories[chat_id]

def initialize_chat_manager():
    chat_manager = ChatManager()
    logger.info("Chat Manager initialized successfully.")
    return chat_manager