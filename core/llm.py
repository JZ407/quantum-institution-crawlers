"""LLM client initialization."""
import sys, os
import yaml

sys.path.insert(0, 'D:/Claude_code/rag_system/rag_system')
from llm_client import LLMClient

def get_llm():
    cfg_path = 'D:/Claude_code/rag_system/config.yaml'
    cfg = yaml.safe_load(open(cfg_path, encoding='utf-8'))['llm']
    return LLMClient(provider='openai', api_key=cfg['api_key'], api_base=cfg['api_base'],
                     model=cfg['model'], max_tokens=2048, timeout=120)
