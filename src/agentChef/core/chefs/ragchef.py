#!/usr/bin/env python3
"""ragchef.py
R.A.G.C.H.E.F. Research, Augmentation, & Generation, Chef

This module replaces the previous research_thread.py, research_ui.py, and research_utils.py
by leveraging the functionality provided in the crawlers_module.py utilities.

It provides a complete pipeline for:
1. Researching topics using ArXiv, web search, and GitHub repositories
2. Converting research papers to conversation format
3. Expanding datasets with variations and paraphrases
4. Cleaning and validating the expanded datasets

Usage:
    python -m agentChef.ragchef --topic "Your research topic" --mode research
    python -m agentChef.ragchef --input papers_dir/ --mode generate --expand 3 --clean

Author: @Borcherdingl
Date: 04/04/2025
"""

import os
import sys
import re
import shutil
import json
import logging
import argparse
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Union, Tuple

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False
    logging.warning("Ollama not available. Some features will be disabled.")

# Import crawler modules
from agentChef.core.crawlers.crawlers_module import (
    WebCrawlerWrapper, 
    ArxivSearcher, 
    DuckDuckGoSearcher, 
    GitHubCrawler
)

# Import data processing modules
from agentChef.core.generation.conversation_generator import OllamaConversationGenerator
from agentChef.core.augmentation.dataset_expander import DatasetExpander
from agentChef.core.classification.dataset_cleaner import DatasetCleaner
from agentChef.core.ollama.ollama_interface import OllamaInterface  # Add this import

# Optional UI imports - only imported if UI mode is selected
try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PyQt6.QtCore import QThread, pyqtSignal
    HAS_QT = True
except ImportError:
    HAS_QT = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('research_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ResearchSystem")

# Default paths
DEFAULT_DATA_DIR = os.path.join(Path.home(), '.research_system')
os.makedirs(DEFAULT_DATA_DIR, exist_ok=True)

# Ollama integration
try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False
    logger.warning("Ollama not available. Some features will be disabled.")

# Add centralized logging
from agentChef.logs.agentchef_logging import log, setup_file_logging

# Replace existing logger initialization with:
logger = log

from .base_chef import BaseChef

class ResearchManager(BaseChef):
    """RAG (Research Augmentation Generation) Chef implementation."""
    
    def __init__(self, data_dir=DEFAULT_DATA_DIR, model_name="llama3"):
        """Initialize the RAG chef."""
        super().__init__(
            name="ragchef",
            model_name=model_name,
            data_dir=data_dir,
            enable_ui=True
        )
        
        # Setup file logging in the data directory
        setup_file_logging(os.path.join(data_dir, 'logs'))
        logger.info("Initializing ResearchManager...")
        
        self.data_dir = Path(data_dir)
        self.model_name = model_name
        
        # Create necessary directories
        self.papers_dir = self.data_dir / "papers"
        self.datasets_dir = self.data_dir / "datasets"
        self.temp_dir = Path(tempfile.mkdtemp(prefix="research_system_"))
        
        for directory in [self.papers_dir, self.datasets_dir]:
            directory.mkdir(exist_ok=True, parents=True)
        
        # Initialize components with better error handling
        try:
            from agentChef.core.crawlers.crawlers_module import (
                WebCrawlerWrapper, 
                ArxivSearcher, 
                DuckDuckGoSearcher, 
                GitHubCrawler
            )
            
            self.web_crawler = WebCrawlerWrapper()
            self.arxiv_searcher = ArxivSearcher()
            self.ddg_searcher = DuckDuckGoSearcher()
            self.github_crawler = GitHubCrawler(data_dir=str(self.data_dir))
            
            logger.info("Successfully initialized all crawlers")
            
        except ImportError as e:
            logger.error(f"Failed to import crawlers: {str(e)}")
            # Create dummy implementations to prevent None errors
            self.web_crawler = type('DummyWebCrawler', (), {
                'text_search': lambda self, *args, **kwargs: asyncio.coroutine(lambda: [])()
            })()
            self.arxiv_searcher = type('DummyArxivSearcher', (), {
                'search_papers': lambda self, *args, **kwargs: asyncio.coroutine(lambda: [])()
            })()
            self.ddg_searcher = type('DummyDDGSearcher', (), {
                'search': lambda self, *args, **kwargs: asyncio.coroutine(lambda: [])()
            })()
            self.github_crawler = type('DummyGitHubCrawler', (), {
                'search_repositories': lambda self, *args, **kwargs: asyncio.coroutine(lambda: [])()
            })()
        
        # Initialize web searchers
        try:
            self.web_searcher = DuckDuckGoSearcher()
            if not self.web_searcher.searcher:
                logger.warning("DuckDuckGo searcher not available")
                self.web_searcher = None
        except Exception as e:
            logger.error(f"Failed to initialize web searcher: {e}")
            self.web_searcher = None
        
        # Initialize processing components if Ollama is available
        if HAS_OLLAMA:
            # Basic Ollama interface for use with DatasetExpander and DatasetCleaner
            self.ollama_interface = OllamaInterface(model_name=model_name)
            
            self.conversation_generator = OllamaConversationGenerator(model_name=model_name)
            self.dataset_expander = DatasetExpander(
                ollama_interface=self.ollama_interface,
                output_dir=str(self.datasets_dir / "expanded")
            )
            self.dataset_cleaner = DatasetCleaner(
                ollama_interface=self.ollama_interface,
                output_dir=str(self.datasets_dir / "cleaned")
            )
        
        # Research state
        self.research_state = {
            "topic": "",
            "search_results": [],
            "arxiv_papers": [],
            "github_repos": [],
            "processed_papers": [],
            "conversations": [],
            "expanded_data": [],
            "cleaned_data": []
        }
    
    async def research_topic(self, topic, max_papers=5, max_search_results=10, 
                        include_github=False, github_repos=None, callback=None,
                        model_name=None):  # Add model_name parameter
        """Research a topic using ArXiv, web search, and optionally GitHub."""
        # Update model if provided
        if model_name:
            self.model_name = model_name
            # Update model for components that need it
            if HAS_OLLAMA:
                self.ollama_interface.set_model(model_name)
                self.conversation_generator.set_model(model_name)
                self.dataset_expander.set_model(model_name)
                self.dataset_cleaner.set_model(model_name)

        self.research_state["topic"] = topic
        
        def update_progress(message):
            logger.info(message)
            if callback:
                callback(message)
        
        update_progress(f"Starting research on: {topic}")
        
        # ArXiv search with proper error handling
        try:
            update_progress(f"Searching ArXiv for: {topic}")
            # Check if arxiv_searcher is available and not None
            if self.arxiv_searcher and hasattr(self.arxiv_searcher, 'search_papers'):
                paper_info = await self.arxiv_searcher.search_papers(topic, max_results=max_papers)
                arxiv_papers = paper_info if paper_info else []
                
                update_progress(f"Found {len(arxiv_papers)} relevant ArXiv papers")
                
                # Format paper information
                formatted_papers = []
                for paper in arxiv_papers:
                    update_progress(f"Processing paper: {paper.get('title', 'Untitled')}")
                    try:
                        # Create a properly formatted paper object
                        formatted_paper = {
                            "title": paper.get('title', ''),
                            "abstract": paper.get('abstract', ''),
                            "authors": paper.get('authors', []),
                            "content": paper.get('abstract', ''),  # Use abstract as content
                            "formatted_info": paper.get('abstract', ''),  # Add this key for compatibility
                            "metadata": {
                                "arxiv_id": paper.get('arxiv_id', ''),
                                "categories": paper.get('categories', []),
                                "published": paper.get('published', ''),
                                "arxiv_url": paper.get('arxiv_url', ''),
                                "pdf_link": paper.get('pdf_link', '')
                            }
                        }
                        formatted_papers.append(formatted_paper)
                    except Exception as e:
                        logger.error(f"Error formatting paper: {str(e)}")
                        continue

                self.research_state["processed_papers"] = formatted_papers
                
            else:
                logger.warning("ArXiv searcher not available")
                arxiv_papers = []
                formatted_papers = []
            
        except Exception as e:
            logger.error(f"Error searching ArXiv: {str(e)}")
            arxiv_papers = []
            formatted_papers = []

        # Move this OUTSIDE the try/except block - this was the main bug
        self.research_state["arxiv_papers"] = arxiv_papers

        # Perform web search with proper fallback
        update_progress(f"Performing web search for: {topic}")
        try:
            # Use the DDG searcher directly since it's initialized
            if self.ddg_searcher and hasattr(self.ddg_searcher, 'text_search'):
                web_results = await self.ddg_searcher.text_search(topic, max_results=max_search_results)
                if web_results:
                    update_progress(f"Found {len(web_results)} web search results")
                else:
                    update_progress("No web search results found")
            else:
                logger.warning("No web searcher available")
                web_results = []
                
        except Exception as e:
            logger.error(f"Error in web search: {str(e)}")
            web_results = []
        
        self.research_state["search_results"] = web_results
        
        # Process GitHub repositories if requested
        if include_github and github_repos:
            update_progress(f"Processing GitHub repositories")
            repo_results = []
            
            for repo_url in github_repos:
                try:
                    update_progress(f"Analyzing repository: {repo_url}")
                    repo_summary = await self.github_crawler.get_repo_summary(repo_url)
                    repo_results.append({
                        "repo_url": repo_url,
                        "summary": repo_summary
                    })
                except Exception as e:
                    update_progress(f"Error processing repository {repo_url}: {str(e)}")
            
            self.research_state["github_repos"] = repo_results
        
        # Generate a research summary
        summary = self._generate_research_summary()
        self.research_state["summary"] = summary
        
        update_progress("Research completed successfully")
        return self.research_state
    
    async def generate_conversation_dataset(self, papers=None, num_turns=3, 
                                          expansion_factor=3, clean=True, callback=None):
        """
        Generate a conversation dataset from research papers.
        
        Args:
            papers: List of papers to process (if None, uses the papers from research_state)
            num_turns: Number of conversation turns to generate
            expansion_factor: Factor by which to expand the dataset
            clean: Whether to clean the expanded dataset
            callback: Optional callback function for progress updates
            
        Returns:
            Dictionary with generated dataset information
        """
        if not HAS_OLLAMA:
            raise RuntimeError("Ollama is required for dataset generation but not available")
        
        # Helper function for progress updates
        def update_progress(message):
            logger.info(message)
            if callback:
                callback(message)
        
        update_progress("Starting conversation dataset generation")
        
        # Use research papers or provided papers
        if papers is None:
            papers = self.research_state.get("processed_papers", [])
            if not papers:
                update_progress("No papers available for dataset generation")
                return {
                    "error": "No papers available",
                    "conversations": [],
                    "expanded_conversations": [],
                    "cleaned_conversations": []
                }
        
        # Extract paper content
        paper_contents = []
        for paper in papers:
            if isinstance(paper, dict):
                # Try different possible content keys
                content = None
                for key in ["formatted_info", "content", "abstract", "title"]:
                    if key in paper and paper[key]:
                        content = paper[key]
                        break
                
                if content:
                    paper_contents.append(content)
                else:
                    # If no content found, try to use the whole paper as string
                    paper_str = json.dumps(paper, indent=2)
                    if len(paper_str) > 100:  # Only if it has substantial content
                        paper_contents.append(paper_str)
                        update_progress(f"Using full paper data as content")
                    else:
                        update_progress(f"Skipping paper with no usable content: {list(paper.keys())}")
            elif isinstance(paper, str):
                # Assume it's a path to a paper file or direct content
                if os.path.exists(paper):
                    try:
                        with open(paper, 'r', encoding='utf-8') as f:
                            paper_contents.append(f.read())
                    except Exception as e:
                        update_progress(f"Error reading paper file {paper}: {str(e)}")
                else:
                    # Treat as direct content
                    paper_contents.append(paper)
            else:
                update_progress(f"Skipping paper with unknown format: {type(paper)}")
        
        if not paper_contents:
            update_progress("No paper contents available for dataset generation")
            return {
                "error": "No paper contents available",
                "conversations": [],
                "expanded_conversations": [],
                "cleaned_conversations": []
            }
        
        # Generate conversations for each paper
        all_conversations = []
        for i, content in enumerate(paper_contents):
            update_progress(f"Generating conversations for paper {i+1}/{len(paper_contents)}")
            
            # Chunk the content
            chunks = self.conversation_generator.chunk_text(content, chunk_size=2000, overlap=200)
            
            # Generate conversations from each chunk
            for j, chunk in enumerate(chunks[:5]):  # Limit to 5 chunks per paper
                update_progress(f"Processing chunk {j+1}/{min(5, len(chunks))} of paper {i+1}")
                
                conversation = self.conversation_generator.generate_conversation(
                    content=chunk,
                    num_turns=num_turns,
                    conversation_context="research paper"
                )
                
                if conversation:
                    all_conversations.append(conversation)
        
        self.research_state["conversations"] = all_conversations
        update_progress(f"Generated {len(all_conversations)} conversations")
        
        # Expand the conversations
        if expansion_factor > 1:
            update_progress(f"Expanding dataset by factor of {expansion_factor}")
            expanded_conversations = self.dataset_expander.expand_conversation_dataset(
                conversations=all_conversations,
                expansion_factor=expansion_factor,
                static_fields={'human': False, 'gpt': False}  # Make both dynamic
            )
            
            self.research_state["expanded_data"] = expanded_conversations
            update_progress(f"Generated {len(expanded_conversations)} expanded conversations")
        else:
            expanded_conversations = all_conversations
            self.research_state["expanded_data"] = expanded_conversations
        
        # Clean the expanded dataset if requested
        if clean and expanded_conversations:
            update_progress("Cleaning expanded dataset")
            cleaned_conversations = self.dataset_cleaner.clean_dataset(
                original_conversations=all_conversations,
                expanded_conversations=expanded_conversations,
                cleaning_criteria={
                    "fix_hallucinations": True,
                    "normalize_style": True,
                    "correct_grammar": True,
                    "ensure_coherence": True
                }
            )
            
            self.research_state["cleaned_data"] = cleaned_conversations
            update_progress(f"Cleaned {len(cleaned_conversations)} conversations")
            
            # Save the cleaned conversations
            output_path = self.dataset_expander.save_conversations_to_jsonl(
                cleaned_conversations,
                f"cleaned_conversations_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            update_progress(f"Saved cleaned conversations to {output_path}")
            
            return {
                "conversations": all_conversations,
                "expanded_conversations": expanded_conversations,
                "cleaned_conversations": cleaned_conversations,
                "output_path": output_path
            }
        else:
            # Save the expanded conversations
            output_path = self.dataset_expander.save_conversations_to_jsonl(
                expanded_conversations,
                f"expanded_conversations_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            update_progress(f"Saved expanded conversations to {output_path}")
            
            return {
                "conversations": all_conversations,
                "expanded_conversations": expanded_conversations,
                "cleaned_conversations": [],
                "output_path": output_path
            }
    
    async def process_paper_files(self, paper_files, output_format='jsonl', 
                                num_turns=3, expansion_factor=3, clean=True, callback=None):
        """
        Process paper files to generate conversation datasets.
        
        Args:
            paper_files: List of file paths to papers
            output_format: Output format ('jsonl', 'parquet', or 'csv')
            num_turns: Number of conversation turns to generate
            expansion_factor: Factor by which to expand the dataset
            clean: Whether to clean the expanded dataset
            callback: Optional callback function for progress updates
            
        Returns:
            Dictionary with generated dataset information
        """
        if not HAS_OLLAMA:
            raise RuntimeError("Ollama is required for dataset generation but not available")
        
        # Helper function for progress updates
        def update_progress(message):
            logger.info(message)
            if callback:
                callback(message)
        
        update_progress(f"Processing {len(paper_files)} paper files")
        
        # Read paper contents
        paper_contents = []
        for file_path in paper_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    paper_contents.append(content)
                    update_progress(f"Read paper: {Path(file_path).name}")
            except Exception as e:
                update_progress(f"Error reading {file_path}: {str(e)}")
        
        if not paper_contents:
            update_progress("No paper contents could be read")
            return {
                "error": "Failed to read paper files",
                "output_paths": []
            }
        
        # Process each paper
        all_conversations = []
        
        for i, content in enumerate(paper_contents):
            update_progress(f"Generating conversations for paper {i+1}/{len(paper_contents)}")
            
            # Generate conversations using DatasetExpander's helper function
            orig_convs, expanded_convs = self.dataset_expander.generate_conversations_from_paper(
                paper_content=content,
                conversation_generator=self.conversation_generator,
                num_chunks=5,  # Process 5 chunks per paper
                num_turns=num_turns,
                expansion_factor=expansion_factor,
                static_fields={'human': True, 'gpt': False},  # Keep human questions static
                reference_fields=['human']  # Use human questions as reference
            )
            
            all_conversations.extend(orig_convs)
        
        update_progress(f"Generated {len(all_conversations)} total conversations")
        
        # Save in multiple formats
        output_base = f"paper_conversations_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Determine which formats to output
        formats = []
        if output_format == 'all':
            formats = ['jsonl', 'parquet', 'csv']
        else:
            formats = [output_format]
        
        output_files = self.dataset_expander.convert_to_multi_format(
            all_conversations,
            output_base,
            formats=formats
        )
        
        update_progress(f"Saved conversations in formats: {', '.join(formats)}")
        
        return {
            "conversations_count": len(all_conversations),
            "output_paths": output_files
        }
    
    async def _generate_arxiv_queries(self, topic):
        """Generate specific queries for ArXiv based on the research topic."""
        if HAS_OLLAMA:
            try:
                prompt = f"""
                Based on the research topic "{topic}", generate 3 specific 
                search queries optimized for the ArXiv academic search engine.
                
                For each query:
                1. Focus on academic/scientific terminology relevant to this field
                2. Include key concepts and methodologies
                3. Use proper Boolean operators if helpful (AND, OR)
                
                Return only the 3 search queries as a numbered list, with no additional text.
                """
                
                response = ollama.chat(model=self.model_name, messages=[
                    {"role": "system", "content": "You are a scientific research assistant specializing in academic literature searches."},
                    {"role": "user", "content": prompt}
                ])
                
                # Extract queries from the response
                queries_text = response['message']['content']
                queries = re.findall(r'\d+\.\s*(.*)', queries_text)
                
                if not queries:
                    queries = [topic]
                    
                return queries
            except Exception as e:
                logger.error(f"Error generating ArXiv queries: {str(e)}")
                return [topic]
        else:
            # If Ollama not available, just use the topic as a query
            return [topic]
    
    def _generate_research_summary(self):
        """Generate a summary of the research results."""
        # Count items in each category
        arxiv_papers = self.research_state.get("arxiv_papers", [])
        search_results = self.research_state.get("search_results", [])
        github_repos = self.research_state.get("github_repos", [])
        
        # Generate a summary with more details
        summary = f"""# Research Summary: {self.research_state.get('topic', 'Unknown Topic')}

## Overview
- Total ArXiv Papers Found: {len(arxiv_papers)}
- Total Web Search Results: {len(search_results)}
- Total GitHub Repositories: {len(github_repos)}

## ArXiv Papers
"""
        
        # Add detailed paper information
        for i, paper in enumerate(arxiv_papers[:5], 1):
            if isinstance(paper, dict):
                title = paper.get('title', 'Unknown Title').strip()
                authors = paper.get('authors', ['Unknown'])
                arxiv_id = paper.get('arxiv_id', 'N/A')
                categories = paper.get('categories', [])
                published = paper.get('published', '')[:10] if paper.get('published') else 'N/A'
                
                summary += f"\n{i}. **{title}**\n"
                summary += f"   - Authors: {', '.join(authors)}\n"
                summary += f"   - ArXiv ID: {arxiv_id}\n"
                summary += f"   - Categories: {', '.join(categories)}\n"
                summary += f"   - Published: {published}\n"
                
                # Add abstract preview if available
                abstract = paper.get('abstract', '')
                if abstract:
                    preview = abstract[:200] + "..." if len(abstract) > 200 else abstract
                    summary += f"   - Preview: {preview}\n"
        
        # Add web search results if any
        if search_results:
            summary += "\n## Top Web Results\n"
            for i, result in enumerate(search_results[:3], 1):
                title = result.get('title', 'Untitled').strip()
                link = result.get('link', '')
                summary += f"\n{i}. [{title}]({link})\n"
        
        # Add GitHub repositories if any
        if github_repos:
            summary += "\n## GitHub Repositories\n"
            for i, repo in enumerate(github_repos, 1):
                repo_url = repo.get('repo_url', '')
                repo_summary = repo.get('summary', 'No summary available')
                summary += f"\n{i}. [{repo_url}]({repo_url})\n"
                summary += f"   {repo_summary}\n"
        
        # Add research statistics
        summary += "\n## Research Statistics\n"
        summary += f"- Research Topic: {self.research_state.get('topic', 'Unknown')}\n"
        summary += f"- Total Results: {len(arxiv_papers) + len(search_results) + len(github_repos)}\n"
        summary += f"- Research Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        return summary
    
    def cleanup(self):
        """Clean up temporary files."""
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up: {str(e)}")

    async def process(self, input_data: Any) -> Dict[str, Any]:
        """Process input through the research pipeline."""
        if isinstance(input_data, str):
            # Treat string input as research topic
            return await self.research_topic(input_data)
        return {"error": "Invalid input type"}
        
    async def generate(self, **kwargs) -> Dict[str, Any]:
        """Generate conversation dataset."""
        return await self.generate_conversation_dataset(**kwargs)

class ResearchThread(QThread):
    """Thread for running research operations in the background (for UI mode)."""
    
    update_signal = pyqtSignal(str)
    result_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    
    def __init__(self, manager, operation, **kwargs):
        """
        Initialize the research thread.
        
        Args:
            manager: ResearchManager instance
            operation: Operation to perform ('research' or 'generate')
            **kwargs: Additional arguments for the operation
        """
        super().__init__()
        self.manager = manager
        self.operation = operation
        self.kwargs = kwargs
        self.stop_requested = False
    
    def run(self):
        """Run the research thread."""
        try:
            # Create callback function for progress updates
            def update_callback(message):
                self.update_signal.emit(message)
            
            # Add callback to kwargs
            self.kwargs['callback'] = update_callback
            
            # Create and run event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            if self.operation == 'research':
                result = loop.run_until_complete(
                    self.manager.research_topic(**self.kwargs)
                )
                self.result_signal.emit(result)
            elif self.operation == 'generate':
                result = loop.run_until_complete(
                    self.manager.generate_conversation_dataset(**self.kwargs)
                )
                self.result_signal.emit(result)
            elif self.operation == 'process':
                result = loop.run_until_complete(
                    self.manager.process_paper_files(**self.kwargs)
                )
                self.result_signal.emit(result)
            else:
                self.error_signal.emit(f"Unknown operation: {self.operation}")
            
            loop.close()
            
        except Exception as e:
            logger.exception("Error in research thread")
            self.error_signal.emit(f"Error: {str(e)}")
    
    def stop(self):
        """Request the thread to stop."""
        self.stop_requested = True


def main():
    """Main function to parse arguments and run the program."""
    parser = argparse.ArgumentParser(description="Unified Research and Dataset Generation System")
    
    # Mode selection
    parser.add_argument("--mode", type=str, choices=['research', 'generate', 'process', 'analyze', 'ui'],
                       default='research', help="Operation mode")
    
    # Research mode arguments
    research_group = parser.add_argument_group("Research Options")
    research_group.add_argument("--topic", type=str, help="Research topic")
    research_group.add_argument("--max-papers", type=int, default=5,
                              help="Maximum number of papers to process")
    research_group.add_argument("--max-search", type=int, default=10,
                              help="Maximum number of web search results")
    research_group.add_argument("--include-github", action="store_true",
                              help="Include GitHub repositories")
    research_group.add_argument("--github-repos", type=str, nargs='+',
                              help="GitHub repository URLs to include")
    research_group.add_argument("--save-research", type=str,
                              help="Path to save research results")
    
    # Generate/process mode arguments
    generate_group = parser.add_argument_group("Generation Options")
    generate_group.add_argument("--input", type=str, help="Input papers directory or file list")
    generate_group.add_argument("--turns", type=int, default=3,
                              help="Number of turns in generated conversations")
    generate_group.add_argument("--expand", type=int, default=3,
                              help="Dataset expansion factor")
    generate_group.add_argument("--clean", action="store_true", default=True,
                              help="Clean the expanded dataset")
    generate_group.add_argument("--hedging", type=str, choices=['confident', 'balanced', 'cautious'],
                              default='balanced', help="Hedging level for responses")
    generate_group.add_argument("--static-human", action="store_true",
                              help="Keep human messages static")
    generate_group.add_argument("--static-gpt", action="store_true",
                              help="Keep GPT messages static")
    
    # Analysis mode arguments
    analyze_group = parser.add_argument_group("Analysis Options")
    analyze_group.add_argument("--orig-dataset", type=str,
                             help="Path to original dataset for analysis")
    analyze_group.add_argument("--exp-dataset", type=str,
                             help="Path to expanded dataset for analysis")
    analyze_group.add_argument("--analysis-output", type=str,
                             help="Path to save analysis results")
    analyze_group.add_argument("--basic-stats", action="store_true",
                             help="Include basic statistics in analysis")
    analyze_group.add_argument("--quality", action="store_true",
                             help="Include quality analysis")
    analyze_group.add_argument("--comparison", action="store_true",
                             help="Include dataset comparison")
    
    # Common arguments
    parser.add_argument("--format", type=str, choices=['jsonl', 'parquet', 'csv', 'all'],
                       default='jsonl', help="Output format")
    parser.add_argument("--model", type=str, default="llama3",
                       help="Ollama model to use")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_DATA_DIR,
                       help="Output directory")
    
    args = parser.parse_args()
    
    # Create research manager
    manager = ResearchManager(data_dir=args.output_dir, model_name=args.model)
    
    # Check if UI mode is selected
    if args.mode == 'ui':
        if not HAS_QT:
            print("Error: PyQt6 is required for UI mode but not available.")
            print("Install it with: pip install PyQt6 PyQt6-WebEngine")
            return 1
        
        # Import UI classes here to avoid dependency if not needed
        from agentChef.core.ui_components.RagchefUI.ui_module import RagchefUI
        
        app = QApplication(sys.argv)
        ui = RagchefUI(manager)
        ui.show()
        return app.exec()
    
    # Create and run event loop for async operations
    loop = asyncio.get_event_loop()
    
    try:
        if args.mode == 'research':
            if not args.topic:
                print("Error: --topic is required for research mode")
                return 1
            
            # Run research
            result = loop.run_until_complete(
                manager.research_topic(
                    topic=args.topic,
                    max_papers=args.max_papers,
                    max_search_results=args.max_search,
                    include_github=args.include_github,
                    github_repos=args.github_repos,
                    callback=print
                )
            )
            
            # Print summary
            print("\n" + "="*80)
            print(f"Research Summary for '{args.topic}':")
            print("="*80)
            print(f"Found {len(result.get('arxiv_papers', []))} ArXiv papers")
            print(f"Found {len(result.get('search_results', []))} web search results")
            print(f"Found {len(result.get('github_repos', []))} GitHub repositories")
            
            # Save results to JSON
            output_path = Path(args.output_dir) / f"research_{int(time.time())}.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
            
            print(f"\nResearch results saved to: {output_path}")
            
        elif args.mode == 'generate':
            if not args.topic and not args.input:
                print("Error: either --topic or --input is required for generate mode")
                return 1
            
            if args.topic:
                # First research the topic
                print(f"Researching topic: {args.topic}")
                research_result = loop.run_until_complete(
                    manager.research_topic(
                        topic=args.topic,
                        max_papers=args.max_papers,
                        callback=print
                    )
                )
                
                # Then generate conversations
                print("\nGenerating conversations from research papers...")
                generate_result = loop.run_until_complete(
                    manager.generate_conversation_dataset(
                        num_turns=args.turns,
                        expansion_factor=args.expand,
                        clean=args.clean,
                        callback=print
                    )
                )
                
                # Print results
                print("\n" + "="*80)
                print(f"Dataset Generation Results:")
                print("="*80)
                print(f"Generated {len(generate_result.get('conversations', []))} original conversations")
                print(f"Generated {len(generate_result.get('expanded_conversations', []))} expanded conversations")
                print(f"Generated {len(generate_result.get('cleaned_conversations', []))} cleaned conversations")
                print(f"\nOutput saved to: {generate_result.get('output_path', 'unknown')}")
                
            else:
                # Process existing papers
                input_path = Path(args.input)
                
                if input_path.is_dir():
                    # Find all text files in the directory
                    paper_files = list(input_path.glob('*.txt')) + list(input_path.glob('*.md'))
                    
                    if not paper_files:
                        print(f"Error: No paper files found in {input_path}")
                        return 1
                    
                    print(f"Processing {len(paper_files)} paper files from {input_path}")
                    
                    # Process the paper files
                    process_result = loop.run_until_complete(
                        manager.process_paper_files(
                            paper_files=paper_files,
                            output_format=args.format,
                            num_turns=args.turns,
                            expansion_factor=args.expand,
                            clean=args.clean,
                            callback=print
                        )
                    )
                    
                    # Print results
                    print("\n" + "="*80)
                    print(f"Paper Processing Results:")
                    print("="*80)
                    print(f"Processed {len(paper_files)} paper files")
                    print(f"Generated {process_result.get('conversations_count', 0)} conversations")
                    
                    for fmt, path in process_result.get('output_paths', {}).items():
                        if isinstance(path, str):
                            print(f"Output {fmt}: {path}")
                    
                else:
                    print(f"Error: {input_path} is not a directory")
                    return 1
                
        elif args.mode == 'process':
            if not args.input:
                print("Error: --input is required for process mode")
                return 1
            
            input_path = Path(args.input)
            
            if input_path.is_file() and input_path.suffix == '.txt':
                # Process a single paper file
                paper_files = [input_path]
            elif input_path.is_dir():
                # Find all text files in the directory
                paper_files = list(input_path.glob('*.txt')) + list(input_path.glob('*.md'))
            else:
                print(f"Error: {input_path} is not a valid file or directory")
                return 1
            
            if not paper_files:
                print(f"Error: No paper files found in {input_path}")
                return 1
            
            print(f"Processing {len(paper_files)} paper files")
            
            # Process the paper files
            process_result = loop.run_until_complete(
                manager.process_paper_files(
                    paper_files=paper_files,
                    output_format=args.format,
                    num_turns=args.turns,
                    expansion_factor=args.expand,
                    clean=args.clean,
                    callback=print
                )
            )
            
            # Print results
            print("\n" + "="*80)
            print(f"Paper Processing Results:")
            print("="*80)
            print(f"Processed {len(paper_files)} paper files")
            print(f"Generated {process_result.get('conversations_count', 0)} conversations")
            
            for fmt, path in process_result.get('output_paths', {}).items():
                if isinstance(path, str):
                    print(f"Output {fmt}: {path}")
    
    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
        return 130
    except Exception as e:
        print(f"Error: {str(e)}")
        logger.exception("Error in main function")
        return 1
    finally:
        # Clean up
        manager.cleanup()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

# Add new ArXiv search method to ArxivSearcher class
class ArxivSearcher:
    """Class for searching and retrieving ArXiv papers."""
    
    async def search_papers(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search ArXiv for papers matching the query.
        
        Args:
            query: Search query
            max_results: Maximum number of results to return
            
        Returns:
            List of paper metadata dictionaries
        """
        try:
            # Convert the query to a simple search format
            search_query = query.replace(' AND ', ' ').replace(' OR ', ' ')
            search_query = re.sub(r'[()"]', '', search_query)
            
            # Use the underlying arXiv API through oarc_crawlers
            papers = []
            async for paper in self.fetcher.search(search_query, max_results=max_results):
                papers.append(paper)
            return papers
            
        except Exception as e:
            logger.error(f"Error searching ArXiv: {str(e)}")
            return []