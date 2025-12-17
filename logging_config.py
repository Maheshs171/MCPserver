"""
Simplified Logging Configuration for Non-Django Projects (MCP Server, FastAPI)
Provides beautiful, session-based file logging with detailed formatting
Each session gets its own log file: YYYY-MM-DD_SESSION-{session_id}.log
"""
import logging
import os
from datetime import datetime
from pathlib import Path
import threading


# Global dictionary to store loggers per session
_session_loggers = {}
_session_lock = threading.Lock()


def get_session_logger(project_name, session_id, log_level=logging.INFO):
    """
    Get or create a session-specific logger
    Each session gets its own log file: YYYY-MM-DD_SESSION-{session_id}.log
    
    Args:
        project_name: Name of the project (e.g., 'MCP Server')
        session_id: Session ID for this logging session
        log_level: Logging level (default: INFO)
    
    Returns:
        Logger instance configured with session-specific file handler
    """
    if not session_id:
        session_id = 'NO_SESSION'
    
    # Create unique logger key
    logger_key = f"{project_name}_{session_id}"
    
    # Return existing logger if already created
    with _session_lock:
        if logger_key in _session_loggers:
            return _session_loggers[logger_key]
        
        # Create new session-specific logger
        logger = _create_session_logger(project_name, session_id, log_level)
        _session_loggers[logger_key] = logger
        return logger


def _create_session_logger(project_name, session_id, log_level):
    """
    Internal function to create a new session logger with its own file
    """
    # Base log directory
    base_log_dir = r"C:\logs\Evaa agentic logs"
    
    # Create project-specific directory
    project_dir = Path(base_log_dir) / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate session-specific log filename with date and session ID
    date_str = datetime.now().strftime('%Y-%m-%d')
    log_filename = f"{date_str}_SESSION-{session_id}.log"
    log_path = project_dir / log_filename
    
    # Create a unique logger name for this session
    logger_name = f"{project_name}.{session_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Prevent propagation to avoid duplicate logs
    logger.propagate = False
    
    # Create beautiful formatter with detailed information
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | SESSION: %(session_id)s | %(funcName)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Create session-specific file handler
    file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    
    # Create console handler with UTF-8 encoding and error handling for Windows
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
    # Fix Windows console encoding issues with emojis
    import sys
    if sys.platform == 'win32':
        # Try to set console to UTF-8 mode
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except:
            pass  # If it fails, console handler will just skip problematic characters
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


class SessionLoggerAdapter(logging.LoggerAdapter):
    """
    Adapter to automatically include session_id in all log records
    """
    
    def __init__(self, logger, session_id):
        super().__init__(logger, {'session_id': session_id})
        self.session_id = session_id
    
    def process(self, msg, kwargs):
        """Add session_id to extra fields"""
        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra']['session_id'] = self.session_id
        return msg, kwargs


def log_section_separator(logger, title=""):
    """
    Log a beautiful section separator for better readability
    
    Args:
        logger: Logger instance
        title: Optional title for the section
    """
    separator = "=" * 100
    if title:
        logger.info(separator)
        logger.info(f"  {title.upper()}")
        logger.info(separator)
    else:
        logger.info(separator)


def log_dict(logger, data_dict, title="Data"):
    """
    Log a dictionary in a beautiful, readable format
    
    Args:
        logger: Logger instance
        data_dict: Dictionary to log
        title: Title for the data section
    """
    logger.info(f"┌─ {title} " + "─" * (90 - len(title)))
    for key, value in data_dict.items():
        # Truncate long values
        str_value = str(value)
        if len(str_value) > 200:
            str_value = str_value[:200] + "..."
        logger.info(f"│  {key}: {str_value}")
    logger.info(f"└" + "─" * 99)

