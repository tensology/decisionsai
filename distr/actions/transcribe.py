from distr.core.signals import signal_manager
import pyautogui
import re


#MEAT
# the actual transcribe action

def listen(chat_manager, action, data):
    print("Started listening...")        
    response(chat_manager, action, data)


def response(chat_manager, action, data):

    print("Transcribe response function called")

    signal_manager.reset_oracle_color.emit()

    speak = action.get('params', {}).get('speak', False)

    if speak:
        # Emit the signal to open the voice box 
        signal_manager.show_voice_box.emit()
        signal_manager.reset_voice_box.emit() # don't play audio yet

    prompt_response = get_prompt(chat_manager, action, data)

    if action.get('params', {}).get('method', "") == 'dictate':
        # dictate this into the computer
        print(f"Dictating: {prompt_response}")
        # Use pyautogui to type the prompt response quickly
        print(f"Prompt response: {prompt_response}")
        # Check if the first word is "dictate" and remove it if so
        words = prompt_response.split()
        if words and words[0].lower() == "dictate":
            prompt_response = " ".join(words[1:])
        pyautogui.write(prompt_response, interval=0.01)  # Adjust the interval for faster typing        
    else:        
        if action.get('params', {}).get('method', "") == 'speak':
            pass
        else:
            print(f"Refined response: {prompt_response}")
            ai_response = chat_manager.process_prompt(prompt_response)
            prompt_response = ai_response["message"]["content"]
            
            print(f"Original AI response: {prompt_response}")  # Add this line
            cleaned_response = cleanup_response(prompt_response)
            print(f"Cleaned AI response: {cleaned_response}")
            prompt_response = cleaned_response

    if speak:
        chat_manager.start_tts(prompt_response)


def get_prompt(chat_manager, action, data):   
    trigger_sentence = data.get('trigger_sentence', '')
    transcription = data['transcription']
    end_words = action.get('end', {}).get('words', [])

    # Use ChatManager to refine the prompt
    refined_content = chat_manager.refine_prompt(action, trigger_sentence, transcription, end_words)

    if refined_content == "<unrecognised>":
        return "I'm sorry, I couldn't understand that. Could you please rephrase?"

    print(f"Refined content: {refined_content}")

    return refined_content


def cleanup_response(response):
    """Clean up the markdown response from Ollama."""
    # If response is a list, join it into a single string
    if isinstance(response, list):
        response = ' '.join(response)
    
    # Remove markdown formatting
    response = re.sub(r'\*\*(.*?)\*\*', r'\1', response)  # Remove bold
    response = re.sub(r'\*(.*?)\*', r'\1', response)  # Remove italics
    
    # Remove bullet points
    response = re.sub(r'^\s*\*\s+', '', response, flags=re.MULTILINE)
    
    # Remove any remaining special characters
    response = re.sub(r'[#>`]', '', response)
    
    # Collapse multiple spaces into a single space
    response = re.sub(r'\s+', ' ', response)
    
    # Collapse multiple newlines into a single one
    response = re.sub(r'\n+', '\n', response)
    
    # Remove the problematic regex that was removing all spaces
    # response = re.sub(r'\s(?=[a-zA-Z])', '', response)
    
    return response.strip()