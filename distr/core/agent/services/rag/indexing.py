"""
LlamaIndex RAG Service - Document indexing and retrieval using LlamaIndex

This service provides RAG capabilities using LlamaIndex for better performance
compared to LangChain, especially for document indexing and retrieval.
"""

import logging
import os
import json
import re
from typing import List, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext, load_index_from_storage, Document
    from llama_index.core.node_parser import SimpleNodeParser
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.llms.ollama import Ollama
    LLAMA_INDEX_AVAILABLE = True
except ImportError:
    LLAMA_INDEX_AVAILABLE = False
    logger.warning("LlamaIndex not available. Install with: pip install llama-index llama-index-embeddings-ollama llama-index-llms-ollama")

# Try to import OpenAI components
try:
    from llama_index.llms.openai import OpenAI as LlamaIndexOpenAI
    from llama_index.embeddings.openai import OpenAIEmbedding
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.debug("OpenAI LlamaIndex components not available")


class LlamaIndexRAGService:
    """
    RAG service using LlamaIndex for document indexing and retrieval.
    
    LlamaIndex advantages over LangChain:
    - Better performance for document indexing
    - More efficient vector storage
    - Better query optimization
    - Native support for file types
    - More flexible node parsing
    """
    
    def __init__(
        self,
        model_name: str = "ornith:9b",
        embedding_model: str = "nomic-embed-text",
        index_path: Optional[str] = None,
        persist_dir: Optional[str] = None,
        openai_api_key: Optional[str] = None
    ):
        if not LLAMA_INDEX_AVAILABLE:
            raise ImportError("LlamaIndex is not available. Install required packages.")
        
        self.model_name = model_name
        self.embedding_model = embedding_model
        self.index_path = index_path or "./llama_index_storage"
        self.persist_dir = persist_dir or self.index_path
        self.openai_api_key = openai_api_key
        
        # Metadata file for tracking indexed files (for change detection)
        self.metadata_file = os.path.join(self.persist_dir, "indexed_files_metadata.json")
        
        # Detect if using OpenAI model - but validate first
        # Only treat as OpenAI model if it's a known valid model
        from distr.core.llm_factory import is_openai_model as _is_openai
        self.is_openai_model = _is_openai(model_name)
        
        # Initialize components
        self.llm = None
        self.embed_model = None
        self.index = None
        self.query_engine = None
        
        self._initialize()
    
    def _validate_openai_model(self, model_name: str) -> bool:
        """
        Validate if model name is a known OpenAI model.
        
        Args:
            model_name: Model name to validate
            
        Returns:
            True if valid OpenAI model, False otherwise
        """
        if not model_name:
            return False
        
        model_lower = model_name.lower().strip()
        if "/" in model_lower:
            model_lower = model_lower.split("/")[-1]
        
        # Known valid OpenAI models (exact matches)
        valid_models = [
            'gpt-4', 'gpt-4o', 'gpt-4-turbo', 'gpt-4-turbo-preview',
            'gpt-3.5-turbo', 'gpt-3.5-turbo-16k',
            'o1-preview', 'o1-mini', 'o3-mini',
            'gpt-4o-mini', 'gpt-4o-2024-08-06',
            # Add future models here as they become available
        ]
        
        # Check exact match
        if model_lower in [v.lower() for v in valid_models]:
            return True
        
        # Check for versioned models (e.g., gpt-4-0613, gpt-4o-2024-08-06)
        # Pattern: gpt-3 or gpt-4, optional version number, optional -turbo, optional date
        if re.match(r'^gpt-[34]o?(-turbo)?(-\d{4}-\d{2}-\d{2})?$', model_lower):
            return True
        
        # Check for gpt-3.5-turbo variants
        if re.match(r'^gpt-3\.5-turbo(-\d{4}-\d{2}-\d{2})?$', model_lower):
            return True
        
        # Check for o1/o3 models
        if re.match(r'^o[13](-preview|-mini)?$', model_lower):
            return True
        
        # GPT-5 family (e.g. gpt-5, gpt-5.4, gpt-5.4-mini, gpt-5.2-chat)
        if re.match(r'^gpt-5(\.[0-9]+)?(-[a-z0-9][a-z0-9.-]*)*$', model_lower):
            return True
        
        return False
    
    def _initialize(self):
        """Initialize LLM, embeddings, and index."""
        try:
            # Initialize LLM - support both OpenAI and Ollama
            use_openai = False
            if self.is_openai_model and OPENAI_AVAILABLE and self.openai_api_key:
                # Validate model name before trying to use OpenAI
                if self._validate_openai_model(self.model_name):
                    try:
                        self.llm = LlamaIndexOpenAI(
                            model=self.model_name,
                            api_key=self.openai_api_key,
                            temperature=0.1
                        )
                        logger.info(f"Initialized OpenAI LLM: {self.model_name}")
                        use_openai = True
                    except Exception as e:
                        logger.warning(f"Failed to initialize OpenAI LLM with model '{self.model_name}': {e}")
                        logger.warning(f"Falling back to Ollama with model '{self.model_name}'")
                        use_openai = False
                else:
                    logger.warning(f"Model '{self.model_name}' is not a recognized OpenAI model, falling back to Ollama")
                    use_openai = False
            
            if not use_openai:
                # Fallback to Ollama
                try:
                    self.llm = Ollama(model=self.model_name, request_timeout=120.0)
                    logger.info(f"Initialized Ollama LLM: {self.model_name}")
                except Exception as e:
                    logger.error(f"Failed to initialize Ollama LLM: {e}")
                    # Try default model as last resort
                    logger.info("Trying default Ollama model: ornith:9b")
                    self.llm = Ollama(model="ornith:9b", request_timeout=120.0)
                    self.model_name = "ornith:9b"
                    logger.info("Initialized Ollama LLM with default model: ornith:9b")
            
            # Initialize embeddings - support both OpenAI and Ollama
            if use_openai:
                try:
                    self.embed_model = OpenAIEmbedding(api_key=self.openai_api_key)
                    logger.info(f"Initialized OpenAI embeddings")
                except Exception as e:
                    logger.warning(f"Failed to initialize OpenAI embeddings: {e}, falling back to Ollama")
                    self.embed_model = OllamaEmbedding(model_name=self.embedding_model)
                    logger.info(f"Initialized Ollama embeddings: {self.embedding_model}")
            else:
                self.embed_model = OllamaEmbedding(model_name=self.embedding_model)
                logger.info(f"Initialized Ollama embeddings: {self.embedding_model}")
            
            # Load or create index
            self._load_or_create_index()
            
        except Exception as e:
            logger.error(f"Error initializing LlamaIndex RAG: {e}")
            # Try to recover with default Ollama model
            try:
                logger.info("Attempting recovery with default Ollama model...")
                self.model_name = "ornith:9b"
                self.is_openai_model = False
                self.llm = Ollama(model="ornith:9b", request_timeout=120.0)
                self.embed_model = OllamaEmbedding(model_name=self.embedding_model)
                self._load_or_create_index()
                logger.info("Recovered successfully with default Ollama model")
            except Exception as recovery_error:
                logger.error(f"Recovery failed: {recovery_error}")
                raise
    
    def _load_file_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Load metadata about previously indexed files."""
        if not os.path.exists(self.metadata_file):
            return {}
        
        try:
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading file metadata: {e}")
            return {}
    
    def _save_file_metadata(self, metadata: Dict[str, Dict[str, Any]]):
        """Save metadata about indexed files."""
        try:
            os.makedirs(os.path.dirname(self.metadata_file), exist_ok=True)
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.warning(f"Error saving file metadata: {e}")
    
    def _get_file_metadata(self, file_path: str) -> Dict[str, Any]:
        """Get current metadata for a file (mtime, size)."""
        try:
            stat = os.stat(file_path)
            return {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "path": file_path
            }
        except Exception as e:
            logger.warning(f"Error getting file metadata for {file_path}: {e}")
            return {}
    
    def _has_file_changed(self, file_path: str, stored_metadata: Dict[str, Any]) -> bool:
        """Check if a file has changed since last indexing."""
        if not stored_metadata:
            return True  # New file
        
        current_metadata = self._get_file_metadata(file_path)
        if not current_metadata:
            return True  # Can't read file, assume changed
        
        # Compare modification time and size
        stored_mtime = stored_metadata.get("mtime", 0)
        stored_size = stored_metadata.get("size", 0)
        current_mtime = current_metadata.get("mtime", 0)
        current_size = current_metadata.get("size", 0)
        
        return current_mtime != stored_mtime or current_size != stored_size
    
    def _load_or_create_index(self):
        """Load existing index or create a new one."""
        import time
        import threading
        
        load_start = time.time()
        current_thread = threading.current_thread().name
        logger.info(f"[RAG] _load_or_create_index started on thread: {current_thread}")
        
        # Ensure persist_dir exists
        os.makedirs(self.persist_dir, exist_ok=True)
        
        try:
            # Check if index files exist (docstore.json is required for a valid index)
            docstore_path = os.path.join(self.persist_dir, "docstore.json")
            index_exists = os.path.exists(docstore_path)
            
            if index_exists:
                # Load existing index from disk
                # NOTE: This loads the ENTIRE index into memory - can be slow for large indexes
                try:
                    load_storage_start = time.time()
                    storage_context = StorageContext.from_defaults(persist_dir=self.persist_dir)
                    storage_duration = time.time() - load_storage_start
                    logger.info(f"[RAG] StorageContext.from_defaults took {storage_duration:.3f}s (thread: {current_thread})")
                    
                    load_index_start = time.time()
                    self.index = load_index_from_storage(storage_context)
                    load_index_duration = time.time() - load_index_start
                    logger.info(f"[RAG] load_index_from_storage took {load_index_duration:.3f}s (thread: {current_thread})")
                    total_load_duration = time.time() - load_start
                    logger.info(f"[RAG] Loaded existing index from {self.persist_dir} (total: {total_load_duration:.3f}s)")
                    logger.warning(f"[RAG] PERFORMANCE: Loading entire index from disk took {total_load_duration:.3f}s - consider incremental loading for large indexes")
                except Exception as load_error:
                    logger.warning(f"[RAG] Failed to load existing index: {load_error}. Creating new index...")
                    # Fall through to create new index
                    index_exists = False
            
            if not index_exists:
                # Create new index with empty documents
                from llama_index.core import Document
                documents = [Document(text="Initial empty index")]
                self.index = VectorStoreIndex.from_documents(
                    documents,
                    embed_model=self.embed_model
                )
                persist_start = time.time()
                self.index.storage_context.persist(persist_dir=self.persist_dir)
                persist_duration = time.time() - persist_start
                logger.info(f"[RAG] Persist operation completed in {persist_duration:.3f}s (thread: {current_thread})")
                logger.info(f"Created new index at {self.persist_dir}")
            
            # Create query engine - wrap in try/except to handle invalid models
            try:
                self.query_engine = self.index.as_query_engine(
                    llm=self.llm,
                    similarity_top_k=5,
                    response_mode="compact"
                )
                logger.info("Query engine initialized")
            except Exception as query_error:
                # Check if it's a model not found error (404) or invalid API key
                error_str = str(query_error)
                is_model_error = '404' in error_str or 'not found' in error_str.lower() or 'model' in error_str.lower()
                
                if is_model_error:
                    # If query engine creation fails due to invalid model, try with default Ollama model
                    logger.info(f"Model '{self.model_name}' not available, falling back to default Ollama model")
                    try:
                        # Use default Ollama model as fallback
                        fallback_llm = Ollama(model="ornith:9b", request_timeout=120.0)
                        self.query_engine = self.index.as_query_engine(
                            llm=fallback_llm,
                            similarity_top_k=5,
                            response_mode="compact"
                        )
                        # Update self.llm to the fallback
                        self.llm = fallback_llm
                        self.model_name = "ornith:9b"
                        self.is_openai_model = False
                        logger.info("Query engine initialized with fallback Ollama model: ornith:9b")
                    except Exception as fallback_error:
                        logger.error(f"Failed to create query engine even with fallback model: {fallback_error}")
                        raise query_error  # Raise original error
                else:
                    # For other errors, log as warning and re-raise
                    logger.warning(f"Failed to create query engine: {query_error}")
                    raise
            
        except Exception as e:
            logger.error(f"Error loading/creating index: {e}")
            raise
    
    def index_directories(self, directories: List[str], exclude_extensions: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Index documents from specified directories.
        
        Uses incremental indexing - only indexes new or changed files.
        Skips files that are already indexed and haven't changed.
        
        Args:
            directories: List of directory paths to index
            exclude_extensions: Optional list of file extensions to exclude (e.g., ['.jpg', '.pdf'])
            
        Returns:
            Dict with indexing results
        """
        import time
        import threading
        
        index_start = time.time()
        current_thread = threading.current_thread().name
        logger.info(f"[RAG] index_directories started on thread: {current_thread}")
        
        if not directories:
            return {"success": False, "error": "No directories provided"}
        
        try:
            # Load existing metadata for change detection (incremental indexing)
            file_metadata = self._load_file_metadata()
            logger.info(f"[RAG] Loaded metadata for {len(file_metadata)} previously indexed files")
            
            # Collect all files to index
            files_to_index = []
            files_skipped_unchanged = []
            excluded_count = 0
            
            for directory in directories:
                if not os.path.exists(directory):
                    logger.warning(f"Directory does not exist: {directory}")
                    continue
                
                directory_path = Path(directory)
                
                # Walk through directory
                for file_path in directory_path.rglob("*"):
                    if file_path.is_file():
                        file_path_str = str(file_path)
                        
                        # Check if should exclude
                        if exclude_extensions:
                            if file_path.suffix.lower() in [ext.lower() for ext in exclude_extensions]:
                                excluded_count += 1
                                continue
                        
                        # INCREMENTAL INDEXING: Check if file is already indexed and unchanged
                        stored_meta = file_metadata.get(file_path_str, {})
                        if stored_meta and not self._has_file_changed(file_path_str, stored_meta):
                            # File already indexed and hasn't changed - skip it
                            files_skipped_unchanged.append(file_path_str)
                            continue
                        
                        # File is new or changed - add to index list
                        files_to_index.append(file_path_str)
            
            if not files_to_index:
                if files_skipped_unchanged:
                    logger.info(f"[RAG] All {len(files_skipped_unchanged)} files already indexed and unchanged - skipping re-index")
                    return {
                        "success": True,
                        "documents_indexed": 0,
                        "files_processed": 0,
                        "files_skipped": len(files_skipped_unchanged),
                        "excluded": excluded_count,
                        "message": f"All files already indexed ({len(files_skipped_unchanged)} files skipped)"
                    }
                else:
                    return {
                        "success": False,
                        "error": "No files found to index",
                        "excluded": excluded_count
                    }
            
            logger.info(f"[RAG] Indexing {len(files_to_index)} new/changed files (skipping {len(files_skipped_unchanged)} unchanged files) from {len(directories)} directories")
            
            # Read documents
            # Group files by parent directory for SimpleDirectoryReader
            documents = []
            for file_path in files_to_index:
                try:
                    # Read individual file
                    reader = SimpleDirectoryReader(
                        input_files=[file_path],
                        recursive=False
                    )
                    file_docs = reader.load_data()
                    documents.extend(file_docs)
                except Exception as e:
                    logger.warning(f"Failed to read {file_path}: {e}")
                    continue
            
            if not documents:
                return {
                    "success": False,
                    "error": "No documents could be read",
                    "excluded": excluded_count
                }
            
            # Create node parser
            node_parser = SimpleNodeParser.from_defaults(
                chunk_size=1024,
                chunk_overlap=200
            )
            
            # Parse documents into nodes
            nodes = node_parser.get_nodes_from_documents(documents)
            
            # Add nodes to index
            self.index.insert_nodes(nodes)
            
            # Update metadata for newly indexed files (for incremental indexing)
            for file_path in files_to_index:
                file_meta = self._get_file_metadata(file_path)
                if file_meta:
                    file_metadata[file_path] = file_meta
            
            # Save updated metadata
            self._save_file_metadata(file_metadata)
            logger.info(f"[RAG] Updated metadata for {len(files_to_index)} newly indexed files")
            
            # Persist index
            persist_start = time.time()
            logger.info(f"[RAG] Starting persist operation on thread: {current_thread}")
            self.index.storage_context.persist(persist_dir=self.persist_dir)
            persist_duration = time.time() - persist_start
            logger.info(f"[RAG] Persist operation completed in {persist_duration:.3f}s (thread: {current_thread})")
            
            # Recreate query engine with updated index
            self.query_engine = self.index.as_query_engine(
                llm=self.llm,
                similarity_top_k=5,
                response_mode="compact"
            )
            
            total_duration = time.time() - index_start
            logger.info(f"[RAG] index_directories completed in {total_duration:.3f}s: indexed {len(documents)} documents ({len(nodes)} nodes) from {len(files_to_index)} files (skipped {len(files_skipped_unchanged)} unchanged)")
            
            return {
                "success": True,
                "documents_indexed": len(documents),
                "nodes_created": len(nodes),
                "files_processed": len(files_to_index),
                "files_skipped": len(files_skipped_unchanged),
                "excluded": excluded_count
            }
            
        except Exception as e:
            logger.error(f"Error indexing directories: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def index_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Index specific files. Supports md, pdf, txt, doc, docx files.
        Uses DocumentExtractorTool for better text extraction from PDFs and Word docs.
        
        Args:
            file_paths: List of file paths to index
            
        Returns:
            Dict with indexing results
        """
        if not file_paths:
            return {"success": False, "error": "No files provided"}
        
        # Supported file extensions
        supported_extensions = {'.md', '.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx'}
        
        # Load existing metadata for change detection
        file_metadata = self._load_file_metadata()
        
        try:
            documents = []
            skipped_count = 0
            unchanged_count = 0
            files_to_process = []
            
            # Filter files: only process new or changed files
            for file_path in file_paths:
                if not os.path.exists(file_path):
                    logger.warning(f"File does not exist: {file_path}")
                    skipped_count += 1
                    continue
                
                # Check file extension
                file_ext = os.path.splitext(file_path)[1].lower()
                if file_ext not in supported_extensions:
                    logger.info(f"Skipping unsupported file type: {file_path} (extension: {file_ext})")
                    skipped_count += 1
                    continue
                
                # Check if file has changed (incremental indexing)
                stored_meta = file_metadata.get(file_path, {})
                if not self._has_file_changed(file_path, stored_meta):
                    logger.info(f"File unchanged, skipping: {file_path}")
                    unchanged_count += 1
                    continue
                
                files_to_process.append(file_path)
            
            if not files_to_process:
                logger.info(f"All {len(file_paths)} file(s) are unchanged, nothing to index")
                return {
                    "success": True,
                    "documents_indexed": 0,
                    "nodes_created": 0,
                    "files_processed": 0,
                    "files_skipped": skipped_count,
                    "files_unchanged": unchanged_count
                }
            
            logger.info(f"Processing {len(files_to_process)} new/changed file(s) out of {len(file_paths)} total")
            
            # Process only changed/new files
            for file_path in files_to_process:
                
                try:
                    # For PDF, DOC, DOCX, XLS, XLSX - use DocumentExtractorTool for better extraction
                    if file_ext in {'.pdf', '.doc', '.docx', '.xls', '.xlsx'}:
                        try:
                            from distr.core.agent.tools.files.document_extractor import DocumentExtractorTool
                            extractor = DocumentExtractorTool()
                            extracted_text = extractor._run(file_path=file_path, extract_archives=False)
                            
                            if extracted_text and not extracted_text.startswith("Error"):
                                # Create Document from extracted text
                                doc = Document(
                                    text=extracted_text,
                                    metadata={"file_path": file_path, "file_name": os.path.basename(file_path)}
                                )
                                documents.append(doc)
                                logger.info(f"Extracted text from {file_path} using DocumentExtractorTool ({len(extracted_text)} chars)")
                            else:
                                logger.warning(f"DocumentExtractorTool returned error or empty text for {file_path}")
                                # Fallback to SimpleDirectoryReader
                                reader = SimpleDirectoryReader(
                                    input_files=[file_path],
                                    recursive=False
                                )
                                file_docs = reader.load_data()
                                documents.extend(file_docs)
                        except Exception as extract_error:
                            logger.warning(f"DocumentExtractorTool failed for {file_path}: {extract_error}, falling back to SimpleDirectoryReader")
                            # Fallback to SimpleDirectoryReader
                            reader = SimpleDirectoryReader(
                                input_files=[file_path],
                                recursive=False
                            )
                            file_docs = reader.load_data()
                            documents.extend(file_docs)
                    else:
                        # For .md, .txt - use SimpleDirectoryReader (it handles these well)
                        reader = SimpleDirectoryReader(
                            input_files=[file_path],
                            recursive=False
                        )
                        file_docs = reader.load_data()
                        documents.extend(file_docs)
                except Exception as e:
                    logger.warning(f"Failed to read {file_path}: {e}")
                    skipped_count += 1
                    continue
            
            if not documents:
                return {
                    "success": False,
                    "error": "No documents could be read"
                }
            
            # Create node parser
            node_parser = SimpleNodeParser.from_defaults(
                chunk_size=1024,
                chunk_overlap=200
            )
            
            # Parse documents into nodes
            nodes = node_parser.get_nodes_from_documents(documents)
            
            # Add nodes to index
            self.index.insert_nodes(nodes)
            
            # Update metadata for processed files
            for file_path in files_to_process:
                if os.path.exists(file_path):
                    file_metadata[file_path] = self._get_file_metadata(file_path)
            
            # Save updated metadata
            self._save_file_metadata(file_metadata)
            
            # Persist index
            self.index.storage_context.persist(persist_dir=self.persist_dir)
            
            # Recreate query engine
            self.query_engine = self.index.as_query_engine(
                llm=self.llm,
                similarity_top_k=5,
                response_mode="compact"
            )
            
            logger.info(f"Successfully indexed {len(documents)} documents from {len(files_to_process)} files (skipped: {skipped_count}, unchanged: {unchanged_count})")
            
            return {
                "success": True,
                "documents_indexed": len(documents),
                "nodes_created": len(nodes),
                "files_processed": len(files_to_process),
                "files_skipped": skipped_count,
                "files_unchanged": unchanged_count
            }
            
        except Exception as e:
            logger.error(f"Error indexing files: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def query(self, query_text: str) -> Dict[str, Any]:
        """
        Query the RAG system.
        
        Args:
            query_text: Query string
            
        Returns:
            Dict with response and metadata
        """
        if not self.query_engine:
            return {
                "success": False,
                "error": "Query engine not initialized"
            }
        
        try:
            response = self.query_engine.query(query_text)
            
            return {
                "success": True,
                "response": str(response),
                "source_nodes": [
                    {
                        "text": node.node.text[:200] + "..." if len(node.node.text) > 200 else node.node.text,
                        "score": node.score,
                        "metadata": node.node.metadata
                    }
                    for node in response.source_nodes
                ] if hasattr(response, 'source_nodes') else []
            }
            
        except Exception as e:
            logger.error(f"Error querying RAG: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_retriever(self, top_k: int = 5):
        """
        Get a retriever for use with other systems.
        
        Args:
            top_k: Number of results to retrieve
            
        Returns:
            Retriever object
        """
        if not self.index:
            return None
        
        return self.index.as_retriever(similarity_top_k=top_k)
    
    def clear_index(self) -> Dict[str, Any]:
        """
        Clear/reset the RAG index (removes all indexed documents).
        
        Returns:
            Dict with success status
        """
        try:
            # Create a new empty index
            from llama_index.core import Document
            documents = [Document(text="Empty index after clear")]
            self.index = VectorStoreIndex.from_documents(
                documents,
                embed_model=self.embed_model
            )
            self.index.storage_context.persist(persist_dir=self.persist_dir)
            
            # Recreate query engine
            self.query_engine = self.index.as_query_engine(
                llm=self.llm,
                similarity_top_k=5,
                response_mode="compact"
            )
            
            # Clear metadata
            if os.path.exists(self.metadata_file):
                os.remove(self.metadata_file)
            
            logger.info(f"Cleared RAG index at {self.persist_dir}")
            
            return {
                "success": True,
                "message": "Index cleared successfully"
            }
        except Exception as e:
            logger.error(f"Error clearing index: {e}")
            return {
                "success": False,
                "error": str(e)
            }






