import mss
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import base64
from io import BytesIO
import ollama
import asyncio
import pyautogui

def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def draw_mouse_position(img, mouse_x, mouse_y, screen_x, screen_y):
    draw = ImageDraw.Draw(img)
    
    # Draw red circle
    circle_radius = 20
    draw.ellipse(
        [(mouse_x - screen_x - circle_radius, mouse_y - screen_y - circle_radius),
         (mouse_x - screen_x + circle_radius, mouse_y - screen_y + circle_radius)],
        outline='red',
        width=2
    )
    
    # Add coordinates text
    coords_text = f"({mouse_x}, {mouse_y})"
    # Try to load a system font, fall back to default if not found
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except:
        font = ImageFont.load_default()
        
    draw.text(
        (mouse_x - screen_x + circle_radius + 5, mouse_y - screen_y - 10),
        coords_text,
        fill='red',
        font=font
    )
    
    return img

def capture_screens():
    captured_images = []
    mouse_x, mouse_y = pyautogui.position()
    
    # Create screenshots directory if it doesn't exist
    screenshot_dir = './screenshots'
    if not os.path.exists(screenshot_dir):
        os.makedirs(screenshot_dir)

    # Initialize screen capture
    with mss.mss() as sct:
        # Get all monitors
        for i, monitor in enumerate(sct.monitors[1:], 1):
            # Capture screenshot
            screenshot = sct.grab(monitor)
            
            # Convert to PIL Image
            img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)
            
            # Check if mouse is on this screen
            if (monitor['left'] <= mouse_x < monitor['left'] + monitor['width'] and
                monitor['top'] <= mouse_y < monitor['top'] + monitor['height']):
                # Draw mouse position on the image
                img = draw_mouse_position(
                    img, 
                    mouse_x, 
                    mouse_y, 
                    monitor['left'],
                    monitor['top']
                )
                print(f'Mouse found on screen {i} at position ({mouse_x}, {mouse_y})')
            
            # Save the image
            filename = f'screen_{i}.png'
            filepath = os.path.join(screenshot_dir, filename)
            img.save(filepath)
            print(f'Saved screenshot of monitor {i} to {filepath}')
            
            captured_images.append((i, img))
    
    return captured_images

def analyze_screens(captured_images):
    # Convert all images to base64
    base64_images = [image_to_base64(img) for _, img in captured_images]
    
    # Create a combined prompt for all screens
    prompt = """
    These are screenshots of what I currently see on my screen.
    explain is excessive details what you see, but give me context of what is going on, do you think.
    """
    
    try:
        # Send all images at once to Llava
        response = ollama.generate(
            model='llava:latest',
            prompt=prompt,
            images=base64_images
        )
        return response['response']
    except Exception as e:
        return f"Error analyzing images: {str(e)}"

def main():
    # Capture all screens
    captured_images = capture_screens()
    
    # Analyze all screens at once
    print("\nAnalyzing all screens...")
    analysis = analyze_screens(captured_images)
    
    print("\nAnalysis Results:")
    print("-" * 50)
    print(analysis)
    print("-" * 50)

if __name__ == "__main__":
    main()