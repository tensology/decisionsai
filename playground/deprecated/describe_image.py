from ollama import Client
import os

def analyze_image(image_path):
    try:
        # Create Ollama client
        client = Client(host='http://localhost:11434')
        
        # Read the image file
        with open(image_path, 'rb') as img_file:
            image_data = img_file.read()
        
        # Generate response for the image
        response = client.generate(
            model='llava',
            prompt='What is in this picture?',
            images=[image_data]
        )
        
        return response['response']
        
    except FileNotFoundError:
        return "Error: Image file not found"
    except Exception as e:
        return f"An error occurred: {str(e)}"

def main():
    # Expand the home directory path
    image_path = os.path.expanduser("~/webcam.jpg")
    
    print("Analyzing image...")
    result = analyze_image(image_path)
    print("\nLLaVA's description:")
    print(result)

if __name__ == "__main__":
    main()
