import ollama
from ollama import Client

def get_optimized_client():
    """
    Returns an optimized Ollama client with settings tuned for low latency streaming.
    """
    client = Client(
        host='http://localhost:11434',
        timeout=30,  # Increased timeout for long responses
    )
    return client

def configure_ollama():
    """
    Configure Ollama with optimized settings for low latency streaming.
    """
    try:
        # Set global Ollama configuration
        ollama.set_host('http://localhost:11434')
        ollama.set_timeout(30)
        
        # Test the configuration
        client = get_optimized_client()
        response = client.chat(
            model='gemma2:latest',
            messages=[{"role": "user", "content": "test"}],
            stream=True,
            options={
                "temperature": 0.7,  # Balanced temperature for response quality
                "top_p": 0.9,  # Slightly reduced top_p for more focused responses
                "top_k": 40,  # Reduced top_k for faster token selection
                "num_ctx": 2048,  # Reduced context window for faster processing
                "num_thread": 4,  # Optimize thread usage
                "num_gpu": 1,  # Use single GPU if available
                "num_batch": 1,  # Process one batch at a time for minimal latency
                "repeat_penalty": 1.1,  # Slight penalty for repetition
                "seed": 42,  # Fixed seed for consistency
                "stop": None,  # No stop sequences for faster processing
                "tfs_z": 1.0,  # Default TFS value
                "num_predict": 100,  # Limit prediction length for faster responses
                "rope_frequency_base": 10000,  # Default RoPE frequency
                "rope_frequency_scale": 1.0,  # Default RoPE scale
            }
        )
        # Consume the stream to verify it works
        for chunk in response:
            if chunk and 'message' in chunk:
                break
                
        return True
    except Exception as e:
        print(f"Error configuring Ollama: {e}")
        return False 