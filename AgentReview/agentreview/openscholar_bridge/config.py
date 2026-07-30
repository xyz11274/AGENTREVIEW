# Researcher/OpenScholar/config.py
'''
"""
OpenScholar API Configuration File
Please modify the following configuration parameters according to your actual environment
"""

class OpenScholarConfig:
    def __init__(self):
        # ===== API Key Configuration =====
        # Semantic Scholar API Key (Required)
        # Apply at: https://www.semanticscholar.org/product/api
        self.S2_API_KEY = "YOUR_SEMANTIC_SCHOLAR_API_KEY"
        
        # vLLM API Key (can be any string, used for API calls)
        self.API_KEY = "sk-your-api-key-here"
        
        # ===== Model Configuration =====
        # Large model name (for main inference)
        self.LARGE_MODEL_NAME = "OpenSciLM/Llama-3.1_OpenScholar-8B"
        
        # Small model name (for search keyword generation)
        self.SMALL_MODEL_NAME = "Qwen/Qwen3-0.6B"
        
        # Reranker model path (please replace with your actual path)
        self.RERANKER_PATH = "OpenSciLM/OpenScholar_Reranker"
        
        # ===== Port Configuration =====
        # Large model service port
        self.LARGE_MODEL_PORT = 38011
        
        # Small model service port
        self.SMALL_MODEL_PORT = 38014
        
        # API service port
        self.API_PORT = 38015
        
        # ===== Processing Parameters =====
        # Search generation batch size
        self.SEARCH_BATCH_SIZE = 100
        
        # OpenScholar processing batch size
        self.SCHOLAR_BATCH_SIZE = 100
        
        # Number of papers to retrieve
        self.TOP_N = 10
        
        # Maximum tokens for generation
        self.MAX_TOKENS = 3000
        
        # ===== GPU Configuration =====
        # CUDA device setting (optional)
        self.CUDA_VISIBLE_DEVICES = "0"
        
        # Large model GPU memory utilization
        self.LARGE_MODEL_GPU_MEMORY_UTILIZATION = 0.8
        
        # Small model GPU memory utilization
        self.SMALL_MODEL_GPU_MEMORY_UTILIZATION = 0.05
        
        # Large model maximum length
        self.LARGE_MODEL_MAX_LEN = 70000
        
        # Small model maximum length
        self.SMALL_MODEL_MAX_LEN = 10000

# Create default configuration instance
default_config = OpenScholarConfig() 

'''