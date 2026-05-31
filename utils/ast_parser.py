import ast
import os
import logging

logger = logging.getLogger(__name__)

def extract_local_imports(filepath: str, repo_root: str) -> list[str]:
    """
    Parses a Python file using AST and returns a list of absolute paths to local modules it imports.
    This gives the LLM the exact dependencies it needs to understand the class relationships.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
            
        tree = ast.parse(source)
    except Exception as e:
        logger.warning(f"AST Parser: Failed to parse {filepath}: {e}")
        return []

    local_dependencies = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # e.g., import utils.database
                _resolve_import(alias.name, repo_root, local_dependencies)
                
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # e.g., from utils.database import Database
                _resolve_import(node.module, repo_root, local_dependencies)
                
    return list(set(local_dependencies))

def _resolve_import(module_name: str, repo_root: str, dependencies: list):
    """
    Attempts to map a Python module name to a local file in the repo.
    """
    # Convert 'utils.database' -> 'utils/database.py' or 'utils/database/__init__.py'
    parts = module_name.split('.')
    
    # 1. Check direct file
    file_path = os.path.join(repo_root, *parts) + ".py"
    if os.path.exists(file_path):
        dependencies.append(file_path)
        return
        
    # 2. Check directory with __init__.py
    dir_path = os.path.join(repo_root, *parts, "__init__.py")
    if os.path.exists(dir_path):
        dependencies.append(dir_path)
        return
