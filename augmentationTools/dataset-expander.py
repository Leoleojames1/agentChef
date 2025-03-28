import pandas as pd
import json
import re
import logging
import os
from typing import List, Dict, Any, Optional, Union, Tuple
from tqdm import tqdm
import numpy as np
import random

class DatasetExpander:
    """
    A class to expand datasets by generating paraphrases and variations of conversation data,
    with control over which fields remain static and which are dynamically generated.
    Works with conversation data in the format produced by OllamaConversationGenerator.
    """
    
    def __init__(self, ollama_interface, output_dir="./output"):
        """
        Initialize the DatasetExpander.
        
        Args:
            ollama_interface: An interface to Ollama for generating text
            output_dir (str): Directory to save expanded datasets
        """
        self.ollama_interface = ollama_interface
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        logging.basicConfig(level=logging.INFO, 
                           format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    def expand_conversation_dataset(self, 
                                  conversations: List[List[Dict[str, str]]], 
                                  expansion_factor: int = 3,
                                  static_fields: Dict[str, bool] = None,
                                  reference_fields: List[str] = None) -> List[List[Dict[str, str]]]:
        """
        Expand a dataset of conversations by generating paraphrases.
        
        Args:
            conversations: List of conversations, where each conversation is a list of turns
                         Each turn is a dict with 'from' and 'value' keys
            expansion_factor: Number of variations to generate for each conversation
            static_fields: Dict mapping field names ("human", "gpt") to boolean indicating if they should remain static
                         If None, defaults to {'human': False, 'gpt': False} (all fields are dynamic)
            reference_fields: List of fields to use as reference values when generating paraphrases
        
        Returns:
            List of expanded conversations
        """
        if static_fields is None:
            static_fields = {'human': False, 'gpt': False}
        
        if reference_fields is None:
            reference_fields = []
            
        expanded_conversations = []
        
        for i, conversation in enumerate(tqdm(conversations, desc="Expanding conversations")):
            self.logger.info(f"Expanding conversation {i+1}/{len(conversations)}")
            
            # Create variations of this conversation
            for j in range(expansion_factor):
                expanded_conversation = []
                
                # Extract reference values from the original conversation if needed
                reference_values = {}
                for turn in conversation:
                    if turn['from'] in reference_fields:
                        reference_values[turn['from']] = turn['value']
                
                # Process each turn in the conversation
                for k, turn in enumerate(conversation):
                    source = turn['from']  # 'human' or 'gpt'
                    
                    if static_fields.get(source, False):
                        # Keep this field static (unchanged)
                        expanded_conversation.append(turn.copy())
                    else:
                        # Generate a paraphrase for this turn
                        original_value = turn['value']
                        paraphrased_value = self.paraphrase_text(
                            original_value, 
                            reference_values,
                            is_question=self._is_question(original_value)
                        )
                        
                        expanded_conversation.append({
                            'from': source,
                            'value': paraphrased_value
                        })
                
                expanded_conversations.append(expanded_conversation)
                
        return expanded_conversations
    
    def paraphrase_text(self, text: str, reference_values: Dict[str, str] = None, is_question: bool = None) -> str:
        """
        Generate a paraphrase of the given text.
        
        Args:
            text: Text to paraphrase
            reference_values: Dictionary of reference values to incorporate
            is_question: Whether the text is a question (if None, will be detected automatically)
        
        Returns:
            Paraphrased text
        """
        if not text.strip():
            return text
            
        if is_question is None:
            is_question = self._is_question(text)
            
        if reference_values is None:
            reference_values = {}
            
        system_prompt = """You are a paraphrasing assistant. Your task is to rephrase the given text while 
        maintaining its original meaning and incorporating any provided reference values. 
        Do not add any explanatory text or meta-information."""
        
        user_prompt = f"""Original text: {text}
        Reference values: {reference_values}
        Is question: {is_question}
        
        Please rephrase the text, maintaining its core meaning and incorporating the reference values where appropriate. 
        If it's a question, keep it as a question. If it's a statement, keep it as a statement.
        Ensure the paraphrased text is coherent and contextually relevant.
        Provide only the paraphrased text without any additional explanations or formatting."""

        try:
            response = self.ollama_interface.chat(messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ])
            
            paraphrased_text = response['message']['content'].strip()
            
            # Verify and clean the paraphrased text
            verified_text = self.verify_paraphrase(
                original=text,
                paraphrased=paraphrased_text,
                reference=reference_values,
                is_question=is_question
            )
            
            # Final cleaning
            cleaned_text = self.clean_generated_content(verified_text, is_question)
            
            return cleaned_text
            
        except Exception as e:
            self.logger.error(f"Error paraphrasing text: {str(e)}")
            return text  # Return original text on error
    
    def verify_paraphrase(self, original: str, paraphrased: str, reference: Dict[str, str], is_question: bool) -> str:
        """
        Verify that the paraphrased text maintains the meaning of the original.
        
        Args:
            original: Original text
            paraphrased: Paraphrased text
            reference: Reference values
            is_question: Whether the text is a question
            
        Returns:
            Verified or corrected paraphrased text
        """
        system_prompt = """You are a verification assistant. Your task is to ensure that the paraphrased content 
        maintains the original meaning, format (question or statement), and incorporates the reference values correctly.
        If the paraphrase is accurate, return it as-is. If not, provide a corrected version."""
        
        user_prompt = f"""Original: {original}
        Paraphrased: {paraphrased}
        Reference values: {reference}
        Is question: {is_question}
        
        Verify that the paraphrased content maintains the original meaning, format (question or statement), 
        and correctly incorporates the reference values. If it does, return the paraphrased content. 
        If not, provide a corrected version that accurately reflects the original meaning, format, 
        and includes the reference values.
        Do not include any explanatory text or meta-information in your response."""

        try:
            response = self.ollama_interface.chat(messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ])
            
            verified_text = response['message']['content'].strip()
            
            # Ensure the verified text has the right format based on is_question
            if is_question and not verified_text.endswith('?'):
                verified_text += '?'
            elif not is_question and verified_text.endswith('?'):
                verified_text = verified_text[:-1] + '.'
                
            return verified_text
            
        except Exception as e:
            self.logger.error(f"Error verifying paraphrase: {str(e)}")
            return paraphrased  # Return the unverified paraphrase on error
    
    def clean_generated_content(self, text: str, is_question: bool) -> str:
        """
        Clean generated content by removing explanatory phrases, normalizing punctuation, etc.
        
        Args:
            text: Text to clean
            is_question: Whether the text is a question
            
        Returns:
            Cleaned text
        """
        # Remove any explanatory phrases or meta-information
        text = re.sub(r'^(Generated content:|Verified content:|Corrected version:)\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*(Verification result:.*|Reference Command:.*|Note:.*|Verified Response:.*)$', '', text, flags=re.IGNORECASE)
        
        # Remove any remaining placeholder-like patterns
        text = re.sub(r'___[A-Za-z_]+___', '', text)
        
        # Remove any quotes that might have been added
        text = text.strip('"\'')
        
        # Remove any leading/trailing whitespace
        text = text.strip()
        
        # Ensure the text starts with a capital letter
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        
        # Ensure the text ends with proper punctuation
        if is_question and not text.endswith('?'):
            text += '?'
        elif not is_question and not text[-1] in '.!?':
            text += '.'
        
        return text
    
    def generate_conversations_from_paper(self, 
                                         paper_content: str, 
                                         conversation_generator,
                                         num_chunks: int = 5,
                                         num_turns: int = 3,
                                         expansion_factor: int = 2,
                                         static_fields: Dict[str, bool] = None,
                                         reference_fields: List[str] = None) -> Tuple[List[List[Dict[str, str]]], List[List[Dict[str, str]]]]:
        """
        Generate conversations from a paper and then expand the dataset.
        
        Args:
            paper_content: The content of the research paper
            conversation_generator: An instance of OllamaConversationGenerator
            num_chunks: Number of chunks to create from the paper
            num_turns: Number of turns per conversation
            expansion_factor: Number of variations to create per conversation
            static_fields: Dict mapping field names to boolean indicating if they should remain static
            reference_fields: List of fields to use as reference when generating paraphrases
            
        Returns:
            Tuple of (original conversations, expanded conversations)
        """
        self.logger.info("Generating conversations from paper content")
        
        # Chunk the paper content
        chunks = conversation_generator.chunk_text(
            paper_content, 
            chunk_size=2000, 
            overlap=200
        )
        
        # Limit to the requested number of chunks
        if len(chunks) > num_chunks:
            # Take evenly spaced chunks rather than just the first N
            indices = np.linspace(0, len(chunks)-1, num_chunks, dtype=int)
            chunks = [chunks[i] for i in indices]
        
        # Generate conversations for each chunk
        conversations = []
        for i, chunk in enumerate(tqdm(chunks, desc="Generating conversations")):
            self.logger.info(f"Generating conversation {i+1}/{len(chunks)}")
            conversation = conversation_generator.generate_conversation(
                content=chunk,
                num_turns=num_turns,
                conversation_context="research paper"
            )
            
            if conversation:
                conversations.append(conversation)
        
        # Expand the conversations
        if conversations:
            expanded_conversations = self.expand_conversation_dataset(
                conversations,
                expansion_factor=expansion_factor,
                static_fields=static_fields,
                reference_fields=reference_fields
            )
        else:
            expanded_conversations = []
        
        return conversations, expanded_conversations
    
    def save_conversations_to_jsonl(self, conversations: List[List[Dict[str, str]]], filename: str) -> str:
        """
        Save conversations to a JSONL file.
        
        Args:
            conversations: List of conversations to save
            filename: Name of the output file (without extension)
            
        Returns:
            Path to the saved file
        """
        output_path = os.path.join(self.output_dir, f"{filename}.jsonl")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for conversation in conversations:
                f.write(json.dumps(conversation) + '\n')
                
        self.logger.info(f"Saved {len(conversations)} conversations to {output_path}")
        return output_path
    
    def save_conversations_to_parquet(self, conversations: List[List[Dict[str, str]]], filename: str) -> str:
        """
        Save conversations to a Parquet file.
        
        Args:
            conversations: List of conversations to save
            filename: Name of the output file (without extension)
            
        Returns:
            Path to the saved file
        """
        # Convert the conversations to a format suitable for Parquet
        data = []
        for i, conversation in enumerate(conversations):
            data.append({
                'conversation_id': i,
                'conversation': json.dumps(conversation)
            })
            
        df = pd.DataFrame(data)
        output_path = os.path.join(self.output_dir, f"{filename}.parquet")
        df.to_parquet(output_path, engine='pyarrow')
        
        self.logger.info(f"Saved {len(conversations)} conversations to {output_path}")
        return output_path
    
    def load_conversations_from_jsonl(self, file_path: str) -> List[List[Dict[str, str]]]:
        """
        Load conversations from a JSONL file.
        
        Args:
            file_path: Path to the JSONL file
            
        Returns:
            List of conversations
        """
        conversations = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                conversation = json.loads(line.strip())
                conversations.append(conversation)
                
        self.logger.info(f"Loaded {len(conversations)} conversations from {file_path}")
        return conversations
    
    def convert_conversations_to_dataframe(self, conversations: List[List[Dict[str, str]]]) -> pd.DataFrame:
        """
        Convert conversations to a DataFrame format with human-gpt alternating pairs.
        
        Args:
            conversations: List of conversations
            
        Returns:
            DataFrame with columns for human input and gpt output
        """
        data = []
        
        for conversation in conversations:
            # Group by pairs (human followed by gpt)
            for i in range(0, len(conversation)-1, 2):
                if i+1 < len(conversation):
                    if conversation[i]['from'] == 'human' and conversation[i+1]['from'] == 'gpt':
                        data.append({
                            'instruction': "Answer the user's question about the research paper.",
                            'input': conversation[i]['value'],
                            'output': conversation[i+1]['value']
                        })
        
        return pd.DataFrame(data)
    
    def convert_to_multi_format(self, conversations: List[List[Dict[str, str]]], 
                              base_filename: str, 
                              formats: List[str] = ['jsonl', 'parquet', 'csv', 'df']):
        """
        Convert conversations to multiple formats and save them.
        
        Args:
            conversations: List of conversations
            base_filename: Base name for the output files
            formats: List of output formats to generate
            
        Returns:
            Dictionary mapping format names to output paths
        """
        output_files = {}
        
        if 'jsonl' in formats:
            output_files['jsonl'] = self.save_conversations_to_jsonl(conversations, base_filename)
            
        if 'parquet' in formats:
            output_files['parquet'] = self.save_conversations_to_parquet(conversations, base_filename)
            
        if 'df' in formats or 'csv' in formats:
            df = self.convert_conversations_to_dataframe(conversations)
            output_files['df'] = df
            
            if 'csv' in formats:
                csv_path = os.path.join(self.output_dir, f"{base_filename}.csv")
                df.to_csv(csv_path, index=False)
                output_files['csv'] = csv_path
                self.logger.info(f"Saved DataFrame to CSV: {csv_path}")
        
        return output_files
    
    def _is_question(self, text: str) -> bool:
        """
        Determine if the text is a question.
        
        Args:
            text: Text to analyze
            
        Returns:
            True if the text is a question, False otherwise
        """
        text = text.strip().lower()
        return (text.endswith('?') or 
                text.startswith(('what', 'when', 'where', 'who', 'why', 'how', 'can', 'could',
                                'would', 'should', 'is', 'are', 'do', 'does', 'will', 'may')))


# Example usage
if __name__ == "__main__":
    import ollama
    
    # Define a simple ollama_interface
    class OllamaInterface:
        def __init__(self, model_name="llama3"):
            self.model = model_name
            
        def chat(self, messages):
            return ollama.chat(model=self.model, messages=messages)
    
    # Initialize the expander
    ollama_interface = OllamaInterface(model_name="llama3")
    expander = DatasetExpander(ollama_interface, output_dir="./expanded_data")
    
    # Import the conversation generator
    from conversation_generator import OllamaConversationGenerator
    generator = OllamaConversationGenerator(model_name="llama3")
    
    # Sample paper content
    paper_content = """
    Attention mechanisms have become an integral part of compelling sequence modeling
    and transduction models in various tasks, allowing modeling of dependencies without
    regard to their distance in the input or output sequences. In this paper we present the
    Transformer, a model architecture eschewing recurrence and instead relying entirely
    on an attention mechanism to draw global dependencies between input and output.
    """
    
    # Generate and expand conversations
    orig_conversations, expanded_conversations = expander.generate_conversations_from_paper(
        paper_content=paper_content,
        conversation_generator=generator,
        num_chunks=2,
        num_turns=3,
        expansion_factor=2,
        static_fields={'human': True, 'gpt': False},  # Keep human questions static, vary gpt responses
        reference_fields=['human']  # Use human questions as reference when generating gpt responses
    )
    
    # Save in multiple formats
    output_files = expander.convert_to_multi_format(
        expanded_conversations, 
        "transformer_paper_conversations",
        formats=['jsonl', 'parquet', 'csv']
    )
    
    print(f"Generated files: {output_files}")
