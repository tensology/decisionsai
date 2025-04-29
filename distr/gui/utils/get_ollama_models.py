from datetime import datetime, timedelta
from distr.core.constants import MODELS_DIR
from bs4 import BeautifulSoup
import requests
import json
import os
import time

def scrape_ollama_library():
    input_file_path = os.path.join(MODELS_DIR, 'ollama_models_html.txt')
    output_file_path = os.path.join(MODELS_DIR, 'ollama_models.json')
    
    # Create the MODELS_DIR if it doesn't exist
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    # Check if the file exists and is older than a day
    if os.path.exists(input_file_path) and not is_file_older_than_a_day(input_file_path):
        print("Reading from existing file...")
        with open(input_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
    else:
        print("Scraping website...")
        url = "https://ollama.com/library"
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"Failed to retrieve the page. Status code: {response.status_code}")
            return
        
        content = response.text
        
        # Save the raw HTML content to file
        with open(input_file_path, 'w', encoding='utf-8') as file:
            file.write(content)
    
    # Parse the content and extract information
    soup = BeautifulSoup(content, 'html.parser')
    models = parse_content(soup)
    
    # Save the extracted information to output file as JSON
    with open(output_file_path, 'w', encoding='utf-8') as file:
        json.dump(models, file, indent=2)
    
    print(f"Extracted information saved to {output_file_path}")
    return models

def parse_content(soup):
    models = []
    li_elements = soup.find_all('li', class_='flex items-baseline border-b border-neutral-200 py-6')
    
    for li in li_elements:
        model = {}
        
        # Extract name
        name_elem = li.find('h2', class_='truncate text-lg font-medium underline-offset-2 group-hover:underline md:text-2xl')
        if name_elem and name_elem.find('span'):
            model['name'] = name_elem.find('span').text.strip()
        
        # Extract description
        desc_elem = li.find('p', class_='max-w-md break-words')
        if desc_elem:
            model['description'] = desc_elem.text.strip()
        
        # Extract sizes
        sizes = []
        size_elements = li.find_all('span', class_='inline-flex items-center rounded-md bg-[#ddf4ff] px-2 py-[2px] text-xs sm:text-[13px] font-medium text-blue-600')
        for size_elem in size_elements:
            sizes.append(size_elem.text.strip())
        if sizes:
            model['sizes'] = sizes
        
        # Extract pulls, tags, and last updated
        stats_elem = li.find('p', class_='my-2 flex space-x-5 text-[13px] font-medium text-neutral-500')
        if stats_elem:
            spans = stats_elem.find_all('span', class_='flex items-center')
            for span in spans:
                text = span.text.strip()
                if 'Pulls' in text:
                    model['pulls'] = text.split()[0].replace(',', '')
                elif 'Tags' in text:
                    model['tags'] = text.split()[0]
                elif 'Updated' in text:
                    model['last_updated'] = ' '.join(text.split()[1:])
        
        models.append(model)
    
    return models

def is_file_older_than_a_day(file_path):
    file_time = os.path.getmtime(file_path)
    current_time = time.time()
    return (current_time - file_time) > timedelta(days=1).total_seconds()

def get_ollama_models():
    output_file_path = os.path.join(MODELS_DIR, 'ollama_models.json')
    
    # Check if the JSON file exists and is not older than a day
    if os.path.exists(output_file_path) and not is_file_older_than_a_day(output_file_path):
        with open(output_file_path, 'r', encoding='utf-8') as file:
            return json.load(file)
    
    # If the file doesn't exist or is old, scrape the data and return it
    return scrape_ollama_library()