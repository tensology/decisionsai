import re
import subprocess
import os
from typing import Optional, List, Any, Iterator, Dict
from langchain_community.llms import Ollama
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.prompts import ChatPromptTemplate
from langchain.schema.runnable import RunnablePassthrough
from langchain.schema.output_parser import StrOutputParser
from interpreter import OpenInterpreter
from langchain.callbacks.manager import CallbackManagerForLLMRun
from langchain.llms.base import LLM
from langchain.schema import LLMResult, Generation
from pydantic import Field, BaseModel

# Color codes for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

def color_print(text, color):
    print(f"{color}{text}{RESET}")

def color_code_blocks(text):
    """Colorize code blocks in green."""
    def repl(match):
        return f"{GREEN}{match.group(0)}{RESET}"
    return re.sub(r'```[\s\S]*?```', repl, text)

class OllamaWrapper(LLM, BaseModel):
    ollama_model: Ollama = Field(...)
    supports_vision: bool = Field(default=False)
    vision_renderer: Optional[Any] = Field(default=None)

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, ollama_model: Ollama, **data):
        super().__init__(ollama_model=ollama_model, **data)

    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        response = self.ollama_model.invoke(prompt)
        return response

    @property
    def _llm_type(self) -> str:
        return "ollama_wrapper"

    def run(self, messages: List[dict]) -> Iterator[Dict[str, str]]:
        prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        response = self.ollama_model.invoke(prompt)
        yield {"type": "output", "content": response}

    def generate(self, prompts: List[str], stop: Optional[List[str]] = None, callbacks: Optional[Any] = None, **kwargs: Any) -> LLMResult:
        generations = []
        for prompt in prompts:
            response = self._call(prompt, stop=stop)
            generations.append([Generation(text=response)])
        return LLMResult(generations=generations)

def is_safe_command(command):
    """Check if the command is safe to execute within the home directory."""
    return "~" in command or "$HOME" in command

def execute_command(command):
    """Execute a shell command and return its output."""
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"Error executing command: {e.stderr.strip()}"

def setup_rag():
    print("Initializing RAG components...")
    embeddings = OllamaEmbeddings(model="nomic-embed-text", show_progress=False)
    
    # Load or create FAISS index
    index_path = "./faiss_index"
    if os.path.exists(index_path):
        db = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
    else:
        # If the index doesn't exist, create a simple one with a sample document
        texts = ["This is a sample document for the FAISS index."]
        db = FAISS.from_texts(texts, embeddings)
        db.save_local(index_path)
    
    retriever = db.as_retriever(search_type="similarity", search_kwargs={"k": 3})
    return retriever

def main():
    print("Initializing Ollama model...")
    ollama_llm = Ollama(model="gemma2:latest")
    ollama_wrapper = OllamaWrapper(ollama_model=ollama_llm)
    print("Model initialized successfully.")

    interpreter = OpenInterpreter()
    interpreter.llm = ollama_wrapper
    interpreter.offline = True
    interpreter.verbose = False
    interpreter.disable_telemetry = True
    interpreter.auto_run = True
    interpreter.safe_mode = "off"

    retriever = setup_rag()

    # Add this line to store conversation history
    conversation_history = []

    # Modify the template to include conversation history
    template = """You are a helpful AI assistant. Your task is to assist with this input:
    {question}
     
    Use the following context to inform your response:
    {context}

    Previous conversation:
    {history}

    System Details:
    - My Operating System: MAC OS
    - My Home Directory: ~
    - My Desktop Directory: ~/Desktop
    - My Documents Directory: ~/Documents
    - My Downloads Directory: ~/Downloads
    - My Pictures Directory: ~/Pictures
    - My Videos Directory: ~/Videos
    - My Music Directory: ~/Music
    - My Pictures Directory: ~/Pictures
    - My Videos Directory: ~/Videos
    - My Music Directory: ~/Music
    

    IMPORTANT INSTRUCTIONS:
    1. Provide the simplest possible bash or python 
       command to answer the question.
    2. Always use the full path.
    3. Assume '~/' as the home directory.
    4. Enclose executable commands in triple backticks (```bash or ```python).
    5. Enclose non-executable commands in triple backticks (```copy file-extension).
    6. After the command, briefly explain what it does.

    If you cannot provide a command that meets these criteria, 
    explain why and ask for more information if needed.
    """
    prompt = ChatPromptTemplate.from_template(template)

    # Modify the RAG chain to include history
    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough(), "history": lambda _: "\n".join(conversation_history)}
        | prompt
        | ollama_wrapper
        | StrOutputParser()
    )

    while True:
        input_text = input("Enter your question (or 'quit' to exit): ")
        if input_text.lower() == 'quit':
            break

        color_print("\nSending prompt to AI...", YELLOW)
        initial_response = rag_chain.invoke(input_text)

        # Extract code blocks
        python_code_blocks = re.findall(r'```python\n(.*?)\n```', initial_response, re.DOTALL)
        bash_code_blocks = re.findall(r'```bash\n(.*?)\n```', initial_response, re.DOTALL)

        execution_results = []

        # Execute Bash code blocks
        color_print(f"Executing Bash Commands", YELLOW)
        for code in bash_code_blocks:
            if is_safe_command(code):
                result = execute_command(code)
                execution_results.append(f"Bash execution result: {result}")
                # color_print(f"Bash execution result: {result}", GREEN)
            else:
                msg = f"Bash command not executed: {code}"
                execution_results.append(msg)
                color_print(msg, RED)

        # Refine the response
        refinement_prompt = f"""
        Original question: {input_text}

        Command attempted: {bash_code_blocks[0] if bash_code_blocks else 'No command provided'}

        Execution result: {' '.join(execution_results)}

        Based on this information, provide a direct and concise answer to the original question.
        If the command was not executed, explain why it couldn't be executed and what it
        would have done if it had been allowed to run. Do not suggest alternative commands 
        or ask for further instructions. Do not provide any code blocks. Do not get into 
        the details of the commands or code.
        """

        # color_print("\nSending refinement prompt to AI...", YELLOW)
        refined_response = ollama_wrapper.invoke(refinement_prompt)
        
        color_print("\nRefined AI Response:", YELLOW)
        print(color_code_blocks(refined_response))
        print("\n")

        # Add this line to update conversation history
        conversation_history.append(f"Human: {input_text}\nAI: {refined_response}")

        # Optionally, limit the history to the last N interactions
        conversation_history = conversation_history[-5:]  # Keep only the last 5 interactions


if __name__ == "__main__":
    main()