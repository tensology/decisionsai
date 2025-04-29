import pyautogui
import cv2
import numpy as np
import pytesseract
from PIL import Image
import argparse
import os

# trying to find a way to move the mouse to a word that's on the screen
# the words are in an image, and the image is in one of 4 quadrants
# the mouse needs to move to the correct position on the screen


def save_quadrants(screenshot):
    screen_width, screen_height = screenshot.shape[1], screenshot.shape[0]
    half_width, half_height = screen_width // 2, screen_height // 2
    quarter_width, quarter_height = screen_width // 4, screen_height // 4

    if not os.path.exists('quadrant_images'):
        os.makedirs('quadrant_images')

    # Save full screenshot
    cv2.imwrite('quadrant_images/full_screenshot.png', cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))

    quadrants = [
        ((0, 0), "Top-left"),
        ((half_width, 0), "Top-right"),
        ((0, half_height), "Bottom-left"),
        ((half_width, half_height), "Bottom-right"),
        ((quarter_width, quarter_height), "Center")
    ]

    for (x, y), quadrant_name in quadrants:
        quadrant = screenshot[y:y+half_height, x:x+half_width]
        cv2.imwrite(f'quadrant_images/{quadrant_name}.png', cv2.cvtColor(quadrant, cv2.COLOR_RGB2BGR))

    print("Saved screenshots")

    return quadrants

def process_quadrant(quadrant_name):
    image_path = f'quadrant_images/{quadrant_name}.png'
    image = cv2.imread(image_path)
    
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, processed_image = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    configs = ['--psm 11', '--psm 6', '--psm 3']
    
    all_words = []
    
    for config in configs:
        data = pytesseract.image_to_data(Image.fromarray(processed_image), config=config, output_type=pytesseract.Output.DICT)
        
        for i, word in enumerate(data['text']):
            if word.strip():
                local_x, local_y = data['left'][i], data['top'][i]
                screen_x, screen_y = local_x, local_y
                
                if quadrant_name == "Top-right" or quadrant_name == "Bottom-right":
                    screen_x += image.shape[1]
                if quadrant_name == "Bottom-left" or quadrant_name == "Bottom-right":
                    screen_y += image.shape[0]
                if quadrant_name == "Center":
                    screen_x += image.shape[1] // 2
                    screen_y += image.shape[0] // 2
                
                all_words.append({
                    'word': word.lower(),  # Convert to lowercase here
                    'local_pos': (local_x, local_y),
                    'screen_pos': (screen_x, screen_y)
                })
    
    combined_text = ' '.join(word_info['word'] for word_info in all_words)
        
    return all_words, combined_text

def find_text_in_screenshot(text_to_find):
    screenshot = pyautogui.screenshot()
    screenshot_np = np.array(screenshot)
    
    quadrants = save_quadrants(screenshot_np)

    text_to_find = text_to_find.lower()
    
    match_info = []
    
    for (x, y), quadrant_name in quadrants:
        all_words, combined_text = process_quadrant(quadrant_name)
        
        # Split the text to find into words
        text_to_find_words = text_to_find.lower().split()
        
        # Check each word in the quadrant
        for word_info in all_words:
            word = word_info['word'].lower()
            
            # Check if all words in text_to_find are in the current word
            is_match = False
            for search_word in text_to_find_words:
                if search_word in word:
                    is_match = True
                    break
            
            # If it's a match, add the information
            if is_match:
                if {
                    'text': text_to_find,
                    'quadrant': quadrant_name,
                    'screen_position': word_info['screen_pos'],
                    'local_position': word_info['local_pos'],
                    'full_word': word_info['word']
                } not in match_info:
                    match_info.append({
                        'text': text_to_find,
                        'quadrant': quadrant_name,
                        'screen_position': word_info['screen_pos'],
                        'local_position': word_info['local_pos'],
                        'full_word': word_info['word']
                    })
    return match_info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find text on screen and report its location")
    parser.add_argument("text", type=str, help="Text to find on the screen")
    args = parser.parse_args()

    for i in find_text_in_screenshot(args.text):
        screen_x, screen_y = i['screen_position']
        pyautogui.moveTo(screen_x, screen_y)
        print(f"Moved mouse to coordinates: ({screen_x}, {screen_y})")
        break