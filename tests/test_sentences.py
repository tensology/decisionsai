import os
import sys
import re

# Add the project root to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import logging
import time
from distr.agent.distr.llm import LLMEngine

# Configure logging
logging.basicConfig(level=logging.INFO)

def process_response(response):
    """Process and validate the response for common issues."""
    issues = []
    
    if not response:
        return ["Empty response"]
    
    # Check for URL splitting
    if ".com" in response and not any(url in response for url in ["Tensology.com", "tensology.com"]):
        issues.append("URL splitting detected - '.com' appears separately")
    
    # Check for time formatting
    if re.search(r'at\d{2}:\d{2}:\d{2}', response):
        issues.append("Incorrect time formatting detected")
    
    # Check for sentence structure
    sentences = response.split('. ')
    for sentence in sentences:
        if len(sentence.strip()) < 3:  # Very short sentences
            issues.append(f"Invalid sentence structure: '{sentence}'")
        if sentence.endswith('and'):  # Incomplete sentences
            issues.append(f"Incomplete sentence: '{sentence}'")
    
    return issues

def wait_for_response(llm, timeout=10):
    """Wait for a response from the LLM with timeout."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = llm.get_result()
        if result and result.get('status') == 'success':
            return result.get('response', '')
        time.sleep(0.1)
    return None

def test_llm_responses():
    """Test LLM responses for common formatting issues."""
    logging.info("Starting LLM engine...")
    llm = LLMEngine(
        model_name="gemma3:4b",
        role="You are Ethan, a friendly and efficient AI assistant developed by Paul Hoft at Tensology.com. Your responses should be clear, concise, and properly formatted."
    )
    
    # Start the LLM engine
    llm.start()
    time.sleep(1)  # Give time for initialization
    
    test_cases = [
        {
            "name": "Basic Introduction",
            "prompt": "Tell me about yourself.",
            "expected_issues": ["URL splitting", "time formatting"]
        },
        {
            "name": "URL Handling",
            "prompt": "What is your website?",
            "expected_issues": ["URL splitting"]
        },
        {
            "name": "Short Responses",
            "prompt": "What is your name?",
            "expected_issues": []
        },
        {
            "name": "Complex Response",
            "prompt": "Tell me about your capabilities and limitations.",
            "expected_issues": ["URL splitting", "sentence structure"]
        }
    ]
    
    try:
        for test_case in test_cases:
            logging.info(f"\n=== Test Case: {test_case['name']} ===")
            logging.info(f"Sending prompt: {test_case['prompt']}")
            
            # Clear any previous responses
            while not llm.output_queue.empty():
                llm.output_queue.get_nowait()
            
            # Send the prompt to the LLM
            llm.process_text(test_case['prompt'])
            
            # Wait for and get the response
            response = wait_for_response(llm)
            
            if response:
                logging.info("\n=== Raw Response ===")
                logging.info(response)
                
                issues = process_response(response)
                logging.info("\n=== Detected Issues ===")
                for issue in issues:
                    logging.info(f"- {issue}")
                
                # Validate against expected issues
                unexpected_issues = [issue for issue in issues if not any(expected in issue.lower() for expected in test_case['expected_issues'])]
                if unexpected_issues:
                    logging.warning(f"Unexpected issues found: {unexpected_issues}")
            else:
                logging.error(f"Failed to get response for test case: {test_case['name']} (timeout)")
            
            # Wait between test cases
            time.sleep(2)
    
    finally:
        logging.info("Stopping LLM engine...")
        llm.stop()

if __name__ == "__main__":
    test_llm_responses()
