"""
PDF Page Extractor Tool for LangChain.

This tool quickly finds PDF files by name and extracts specific pages.
Designed for fast, targeted PDF page extraction.
"""

from typing import Optional, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import os
import glob
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class PDFPageExtractorInput(BaseModel):
    """Input schema for pdf_page_extractor tool."""
    pdf_name: str = Field(description="Name or partial name of the PDF file to find (e.g., 'Cameron and Electronics Project')")
    page_number: int = Field(description="Page number to extract (1-indexed)")
    search_folders: Optional[List[str]] = Field(default=None, description="Optional list of folders to search. Defaults to Downloads, Documents, Desktop")


class PDFPageExtractorTool(BaseTool):
    """Tool for quickly finding PDFs and extracting specific pages."""
    
    name: str = "pdf_page_extractor"
    description: str = (
        "Quickly find a PDF file by name and extract a specific page. "
        "Searches common folders (Downloads, Documents, Desktop) by default. "
        "Use this when you need to read a specific page from a PDF document. "
        "Example: 'get page 147 from Cameron and Electronics Project PDF' "
        "or 'read page 10 from the report in my documents'. "
        "This tool is optimized for fast page extraction from PDFs."
    )
    args_schema: type[BaseModel] = PDFPageExtractorInput
    
    def _find_pdf(self, pdf_name: str, search_folders: Optional[List[str]] = None) -> Optional[str]:
        """Find PDF file by name using fuzzy matching."""
        if search_folders is None:
            # Default search folders
            home = os.path.expanduser("~")
            search_folders = [
                os.path.join(home, "Downloads"),
                os.path.join(home, "Documents"),
                os.path.join(home, "Desktop"),
                os.path.join(home, "Documents", "Books"),
            ]
        
        # Normalize search name (remove common extensions, lowercase)
        search_name = pdf_name.lower().strip()
        if search_name.endswith('.pdf'):
            search_name = search_name[:-4]
        
        # Clean up search name
        search_name = search_name.replace('"', '').replace("'", "")
        
        best_match = None
        best_score = 0.0
        
        logger.info(f"PDFPageExtractor: Searching for PDF '{pdf_name}' in {len(search_folders)} folders")
        
        for folder in search_folders:
            if not os.path.exists(folder):
                continue
            
            # Search for PDF files
            patterns = [
                os.path.join(folder, "*.pdf"),
                os.path.join(folder, "**", "*.pdf"),  # Recursive
            ]
            
            for pattern in patterns:
                try:
                    for pdf_path in glob.glob(pattern, recursive=True):
                        pdf_filename = os.path.basename(pdf_path)
                        pdf_name_lower = pdf_filename.lower()
                        
                        # Remove .pdf extension for comparison
                        if pdf_name_lower.endswith('.pdf'):
                            pdf_name_lower = pdf_name_lower[:-4]
                        
                        # Calculate similarity score
                        score = SequenceMatcher(None, search_name, pdf_name_lower).ratio()
                        
                        # Bonus if search name is contained in filename
                        if search_name in pdf_name_lower:
                            score += 0.3
                        
                        if score > best_score:
                            best_score = score
                            best_match = pdf_path
                except Exception as e:
                    logger.warning(f"PDFPageExtractor: Error searching in {folder}: {e}")
                    continue
        
        if best_match and best_score > 0.4:  # Minimum similarity threshold
            logger.info(f"PDFPageExtractor: Found PDF '{best_match}' with similarity {best_score:.2f}")
            return best_match
        else:
            logger.warning(f"PDFPageExtractor: No PDF found matching '{pdf_name}' (best score: {best_score:.2f})")
            return None
    
    def _extract_page(self, pdf_path: str, page_number: int) -> str:
        """Extract text from a specific page of a PDF."""
        try:
            # Try pdfplumber first (better for complex PDFs)
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    total_pages = len(pdf.pages)
                    if page_number < 1 or page_number > total_pages:
                        return f"Error: Page {page_number} is out of range. PDF has {total_pages} pages."
                    
                    page = pdf.pages[page_number - 1]  # 0-indexed
                    page_text = page.extract_text()
                    if page_text:
                        return f"=== Page {page_number} of {total_pages} ===\n\n{page_text}"
                    else:
                        return f"Page {page_number} appears to be empty or contains only images."
            except ImportError:
                # Fallback to PyPDF2
                import PyPDF2
                with open(pdf_path, 'rb') as file:
                    pdf_reader = PyPDF2.PdfReader(file)
                    total_pages = len(pdf_reader.pages)
                    if page_number < 1 or page_number > total_pages:
                        return f"Error: Page {page_number} is out of range. PDF has {total_pages} pages."
                    
                    page = pdf_reader.pages[page_number - 1]  # 0-indexed
                    page_text = page.extract_text()
                    if page_text:
                        return f"=== Page {page_number} of {total_pages} ===\n\n{page_text}"
                    else:
                        return f"Page {page_number} appears to be empty or contains only images."
        except Exception as e:
            logger.error(f"PDFPageExtractor: Error extracting page {page_number} from {pdf_path}: {e}", exc_info=True)
            return f"Error extracting page {page_number}: {str(e)}"
    
    def _run(self, pdf_name: str, page_number: int, search_folders: Optional[List[str]] = None, **kwargs) -> str:
        """Find PDF and extract specific page."""
        # Find the PDF
        pdf_path = self._find_pdf(pdf_name, search_folders)
        if not pdf_path:
            return f"Error: Could not find PDF file matching '{pdf_name}'. Searched in Downloads, Documents, Desktop, and Documents/Books."
        
        # Extract the page
        result = self._extract_page(pdf_path, page_number)
        return result
    
    async def _arun(self, pdf_name: str, page_number: int, search_folders: Optional[List[str]] = None, **kwargs) -> str:
        """Async version of _run."""
        return self._run(pdf_name, page_number, search_folders)

