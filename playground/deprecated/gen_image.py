from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline, AutoPipelineForText2Image
from contextlib import redirect_stdout, redirect_stderr
from huggingface_hub import login
from datetime import datetime
import torch._dynamo
import numpy as np
import traceback
import logging
import shutil
import torch
import sys
import os
import io

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Default Hugging Face token
DEFAULT_TOKEN = "<your-api-key>"

def login_to_huggingface(token):
    try:
        # Redirect stdout and stderr to capture and discard unwanted messages
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            login(token=token)
        return True
    except Exception as e:
        return False

def load_model(model_id):
    try:
        print(f"Attempting to load model: {model_id}")
        
        # Determine the best available device
        if torch.cuda.is_available():
            device = torch.device("cuda")
            print("Using CUDA GPU")
        elif torch.backends.mps.is_available():
            device = torch.device("mps")
            print("Using Apple Silicon (M1/M2/M3) GPU")
        else:
            device = torch.device("cpu")
            print("Using CPU")

        # Load the model
        if "sdxl-turbo" in model_id:
            pipe = AutoPipelineForText2Image.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if device.type in ["cuda", "mps"] else torch.float32,
                use_safetensors=True,
            )
        else:
            pipe = StableDiffusionPipeline.from_pretrained(
                model_id, 
                torch_dtype=torch.float16 if device.type in ["cuda", "mps"] else torch.float32,
                use_safetensors=True,
            )
        
        if not "sdxl-turbo" in model_id:
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
        
        # Move the model to the appropriate device
        pipe = pipe.to(device)
        
        print(f"Model loaded successfully and moved to {device}")
        
        return pipe
    except Exception as e:
        print(f"An error occurred while loading the model: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return None

def generate_image(pipe, prompt, output_path, num_inference_steps=50):
    try:
        # Set safety checker to None to bypass it
        pipe.safety_checker = None
        
        print(f"Generating image with prompt: '{prompt}'")
        print(f"Number of inference steps: {num_inference_steps}")
        print(f"Model device: {pipe.device}")
        
        # Call the appropriate generation method based on the model type
        if isinstance(pipe, AutoPipelineForText2Image):
            image = generate_sdxl_turbo(pipe, prompt)
        else:
            image = generate_standard(pipe, prompt, num_inference_steps)
        
        if image is None:
            print("Error: The model failed to generate an image.")
            return
        
        # Check if the image is all black
        if np.array(image).sum() == 0:
            print("Warning: Generated image is all black. This may indicate an issue with the model or generation process.")
            print("Please try a different model or prompt.")
            return
        
        # Save the image
        image.save(output_path)
        print(f"Image saved to {output_path}")
        
        print(f"Image size: {image.size}")
        print(f"Image mode: {image.mode}")
        
    except Exception as e:
        print(f"An error occurred during image generation: {str(e)}")
        print("Traceback:")
        print(traceback.format_exc())
        print("Please try again or choose a different model.")

def generate_sdxl_turbo(pipe, prompt):
    result = pipe(
        prompt=prompt,
        num_inference_steps=1,  # SDXL-Turbo is designed for fast inference
        guidance_scale=0.0,
    )
    return result.images[0] if result and hasattr(result, 'images') and len(result.images) > 0 else None

def generate_standard(pipe, prompt, num_inference_steps):
    result = pipe(
        prompt, 
        num_inference_steps=num_inference_steps,
        guidance_scale=7.5,
    )
    return result.images[0] if result and hasattr(result, 'images') and len(result.images) > 0 else None

def list_downloaded_models():
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    if not os.path.exists(cache_dir):
        print("No downloaded models found.")
        return []
    
    models = [d for d in os.listdir(cache_dir) if os.path.isdir(os.path.join(cache_dir, d)) and not d.startswith('.')]
    if not models:
        print("No downloaded models found.")
    else:
        print("Downloaded models:")
        for i, model in enumerate(models, 1):
            print(f"{i}. {model}")
    return models

def remove_model(model_name):
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    model_dir = os.path.join(cache_dir, model_name)
    if os.path.exists(model_dir):
        shutil.rmtree(model_dir)
        print(f"Model {model_name} has been removed.")
    else:
        print(f"Model {model_name} not found.")

def main():
    # Get current date and time
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"Welcome to the Image Generation Script!")
    print(f"Current Date and Time: {current_time}")
    
    token = DEFAULT_TOKEN
    if login_to_huggingface(token):
        print("Successfully logged in to Hugging Face")
    else:
        print("Failed to log in. Please update your Hugging Face token.")

    model_options = [
        "runwayml/stable-diffusion-v1-5",
        "CompVis/stable-diffusion-v1-4",
        "stabilityai/stable-diffusion-xl-base-1.0",
        "stabilityai/sdxl-turbo",
        "stabilityai/stable-diffusion-2-1",
        "stabilityai/stable-diffusion-3-medium-diffusers",
        "stabilityai/stable-diffusion-2"
    ]

    while True:
        print("\nMain Menu:")
        print("1. Generate Image")
        print("2. List Downloaded Models")
        print("3. Remove Downloaded Model")
        print("4. Update Hugging Face Token")
        print("5. Quit")

        choice = input("Enter your choice (1-5) or 'q' to quit: ")

        if choice.lower() == 'q':
            print("Exiting program. Goodbye!")
            sys.exit(0)

        if choice == '1':
            print("\nAvailable models:")
            for i, model in enumerate(model_options, 1):
                print(f"{i}: {model}")

            while True:
                model_choice = input(f"Choose a model (1-{len(model_options)}): ")
                try:
                    model_index = int(model_choice) - 1
                    if 0 <= model_index < len(model_options):
                        model_id = model_options[model_index]
                        break
                    else:
                        print("Invalid choice. Please try again.")
                except ValueError:
                    print("Invalid input. Please enter a number.")

            print(f"Loading model {model_id}... This may take a few minutes.")
            pipe = load_model(model_id)
            if pipe is None:
                print("Failed to load the model. Returning to main menu.")
                continue

            print("Model loaded. Ready to generate images!")

            while True:
                prompt = input("Enter your prompt (or 'back' to return to main menu): ")
                if prompt.lower() == 'back':
                    break

                output_path = input("Enter output file name (or press Enter for default 'generated_image.png'): ")
                if not output_path:
                    output_path = "outputs/generated_image.png"
                
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                else:
                    output_path = os.path.join("outputs", output_path)
                    os.makedirs("outputs", exist_ok=True)

                generate_image(pipe, prompt, output_path)
                print("\nImage generation complete.")
                
        elif choice == '2':
            list_downloaded_models()

        elif choice == '3':
            models = list_downloaded_models()
            if models:
                model_index = input("Enter the number of the model you want to remove: ")
                try:
                    model_to_remove = models[int(model_index) - 1]
                    confirm = input(f"Are you sure you want to remove {model_to_remove}? (y/n): ")
                    if confirm.lower() == 'y':
                        remove_model(model_to_remove)
                except (ValueError, IndexError):
                    print("Invalid input. Please try again.")
            else:
                print("No models available to remove.")

        elif choice == '4':
            print("Exiting program. Goodbye!")
            break

        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()