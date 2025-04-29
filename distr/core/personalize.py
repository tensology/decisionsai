
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

class AIAssistant:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'

    def __init__(self, model_name: str = "gemma2:latest"):
        self.ollama_llm = Ollama(model=model_name)
        self.ollama_wrapper = self.OllamaWrapper(ollama_model=self.ollama_llm)
        self.interpreter = self._setup_interpreter()
        self.retriever = self._setup_rag()
        self.conversation_history = []
        self.rag_chain = self._setup_rag_chain()

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
            return self.ollama_model.invoke(prompt)

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

    @staticmethod
    def color_print(text, color):
        print(f"{color}{text}{AIAssistant.RESET}")

    @staticmethod
    def color_code_blocks(text):
        def repl(match):
            return f"{AIAssistant.GREEN}{match.group(0)}{AIAssistant.RESET}"
        return re.sub(r'```[\s\S]*?```', repl, text)

    @staticmethod
    def is_safe_command(command):
        return "~" in command or "$HOME" in command

    @staticmethod
    def execute_command(command):
        try:
            result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Error executing command: {e.stderr.strip()}"

    def _setup_interpreter(self):
        interpreter = OpenInterpreter()
        interpreter.llm = self.ollama_wrapper
        interpreter.offline = True
        interpreter.verbose = False
        interpreter.disable_telemetry = True
        interpreter.auto_run = True
        interpreter.safe_mode = "off"
        return interpreter

    def _setup_rag(self):
        print("Initializing RAG components...")
        embeddings = OllamaEmbeddings(model="nomic-embed-text", show_progress=False)
        index_path = "./faiss_index"
        if os.path.exists(index_path):
            db = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
        else:
            texts = ["This is a sample document for the FAISS index."]
            db = FAISS.from_texts(texts, embeddings)
            db.save_local(index_path)
        return db.as_retriever(search_type="similarity", search_kwargs={"k": 3})

    def _setup_rag_chain(self):
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
        return (
            {"context": self.retriever, "question": RunnablePassthrough(), "history": lambda _: "\n".join(self.conversation_history)}
            | prompt
            | self.ollama_wrapper
            | StrOutputParser()
        )

    def process_input(self, input_text):
        self.color_print("\nSending prompt to AI...", self.YELLOW)
        initial_response = self.rag_chain.invoke(input_text)

        python_code_blocks = re.findall(r'```python\n(.*?)\n```', initial_response, re.DOTALL)
        bash_code_blocks = re.findall(r'```bash\n(.*?)\n```', initial_response, re.DOTALL)

        execution_results = []

        self.color_print(f"Executing Bash Commands", self.YELLOW)
        for code in bash_code_blocks:
            if self.is_safe_command(code):
                result = self.execute_command(code)
                execution_results.append(f"Bash execution result: {result}")
            else:
                msg = f"Bash command not executed: {code}"
                execution_results.append(msg)
                self.color_print(msg, self.RED)

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

        refined_response = self.ollama_wrapper.invoke(refinement_prompt)
        
        self.color_print("\nRefined AI Response:", self.YELLOW)
        print(self.color_code_blocks(refined_response))
        print("\n")

        self.conversation_history.append(f"Human: {input_text}\nAI: {refined_response}")
        self.conversation_history = self.conversation_history[-5:]

    def run(self):
        print("Initializing Ollama model...")
        print("Model initialized successfully.")

        while True:
            input_text = input("Enter your question (or 'quit' to exit): ")
            if input_text.lower() == 'quit':
                break
            self.process_input(input_text)

if __name__ == "__main__":
    assistant = AIAssistant()
    assistant.run()
