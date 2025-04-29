import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''  # Force CPU usage

from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
import torch
torch.set_num_threads(4)  # Limit the number of CPU threads
from pathlib import Path
import json
import random
import Levenshtein
from fuzzywuzzy import fuzz

# Go back one directory from the current file's location
CORE_DIR = str(Path(__file__).resolve().parent.parent)

# Load SBERT model
model = SentenceTransformer('all-MiniLM-L6-v2')

# Load in distr.core.actions.config.json
path = f"{CORE_DIR}/distr/core/actions.config.json"
try:
    with open(path, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"Error: Config file not found at {path}")
    config = []
except json.JSONDecodeError:
    print(f"Error: Invalid JSON in config file at {path}")
    config = []

trigger_words = [action["trigger"] for action in config["actions"] if "trigger" in action]
trigger_descriptions = {action["trigger"]: action.get("method", "") for action in config["actions"] if "trigger" in action}

# Rule-based corrections for phonetically similar words
corrections = {
    "moss": "mouse",
    "moose": "mouse",
    "curse": "cursor",
    "curser": "cursor",
    "right": "write",
    "rite": "right",
    "left": "lift",
    "center": "sender",
    "middle": "medal",
    "scroll": "stroll",
    "move": "mood",
    "press": "dress",
    "enter": "inter",
    "delete": "dilute",
    "space": "pace",
    "tab": "tap",
    "escape": "cape",
    "copy": "coffee",
    "paste": "taste",
    "cut": "cup",
    "undo": "unto",
    "redo": "read",
    "select": "collect",
    "find": "fine",
    "replace": "replay",
    "save": "safe",
    "open": "hoping",
    "close": "clothes",
    "quit": "quick",
    "exit": "exist",
    "minimize": "minimized",
    "maximize": "maximized",
    "restore": "restorer",
    "refresh": "fresh",
    "reload": "reloaded",
    "zoom": "boom",
    "screen": "scream",
    "print": "sprint",
    "mute": "moot",
    "unmute": "unmoot",
    "volume": "volumes",
    "play": "lay",
    "pause": "paws",
    "stop": "top",
    "next": "nest",
    "previous": "previews",
    "forward": "foreword",
    "backward": "backwards",
    "rewind": "remind",
    "fast": "last",
}

def apply_corrections(text):
    words = text.lower().split()
    corrected_words = [corrections.get(word, word) for word in words]
    return " ".join(corrected_words)

def create_training_examples(trigger_words, corrections, trigger_descriptions):
    examples = []
    for trigger in trigger_words:
        description = trigger_descriptions.get(trigger, "")
        
        # Original trigger with description
        examples.append(InputExample(texts=[f"{trigger} - {description}", trigger], label=1.0))
        
        # Variations
        words = trigger.split()
        for _ in range(5):
            variation = []
            for word in words:
                if random.random() < 0.3:
                    variation.append(random.choice(list(corrections.keys())))
                else:
                    variation.append(word)
            variation_text = ' '.join(variation)
            examples.append(InputExample(texts=[f"{variation_text} - {description}", trigger], label=1.0))
        
        # Add negative examples
        for _ in range(2):
            negative = random.choice(trigger_words)
            while negative == trigger:
                negative = random.choice(trigger_words)
            examples.append(InputExample(texts=[f"{trigger} - {description}", negative], label=0.0))
    
    return examples

def fine_tune_model(model, trigger_words, corrections, trigger_descriptions):
    train_examples = create_training_examples(trigger_words, corrections, trigger_descriptions)
    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=32)
    train_loss = losses.MultipleNegativesRankingLoss(model)
    
    model.fit(train_objectives=[(train_dataloader, train_loss)], epochs=3, warmup_steps=100)

def get_closest_trigger(input_text, trigger_words, model, threshold=0.5):
    # Check for exact match first
    if input_text in trigger_words:
        return input_text, 1.0

    # Compute embeddings
    input_embedding = model.encode([input_text])[0]
    trigger_embeddings = model.encode(trigger_words)
    
    # Compute cosine similarities
    similarities = torch.nn.functional.cosine_similarity(
        torch.tensor(input_embedding).unsqueeze(0),
        torch.tensor(trigger_embeddings)
    )
    
    # Adjust similarities based on fuzzy string matching and word order
    for i, trigger in enumerate(trigger_words):
        fuzzy_ratio = fuzz.ratio(input_text.lower(), trigger.lower()) / 100
        word_order_ratio = fuzz.token_sort_ratio(input_text.lower(), trigger.lower()) / 100
        similarities[i] = similarities[i] * 0.5 + fuzzy_ratio * 0.3 + word_order_ratio * 0.2
    
    # Get the index of the highest similarity
    best_match_index = similarities.argmax().item()
    best_match_similarity = similarities[best_match_index].item()
    
    # Return the best match if it's above the threshold, otherwise return None
    if best_match_similarity >= threshold:
        return trigger_words[best_match_index], best_match_similarity
    else:
        return None, 0.0

# Main execution
if __name__ == "__main__":
    model_path = f"{CORE_DIR}/fine_tuned_model"
    
    if os.path.exists(model_path):
        print("Loading fine-tuned model...")
        model = SentenceTransformer(model_path)
    else:
        print("Fine-tuning the model...")
        model = SentenceTransformer('all-MiniLM-L6-v2')
        fine_tune_model(model, trigger_words, corrections, trigger_descriptions)
        print("Saving fine-tuned model...")
        model.save(model_path)
    
    print(f"SBERT model loaded: {model.get_sentence_embedding_dimension()}d")
    print(f"Number of trigger words: {len(trigger_words)}")
    
    try:
        while True:
            input_word = input("\nEnter an ambiguous word (or 'quit' to exit): ").strip().lower()
            
            if input_word == 'quit':
                break
            
            corrected_input = apply_corrections(input_word)
            best_match, similarity = get_closest_trigger(corrected_input, trigger_words, model, threshold=0.5)
            
            if best_match:
                print(f"Best match for '{input_word}':")
                print(f"Trigger: {best_match}")
                print(f"Similarity: {similarity:.4f}")
                print(f"Associated action: {trigger_descriptions.get(best_match, 'Unknown')}")
            else:
                print("No match found above the threshold.")

    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")

    print("Thank you for using the SBERT trigger word finder!")