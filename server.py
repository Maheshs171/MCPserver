# -*- coding: utf-8 -*-
"""
MCP Server for EVAA Virtual Assistant
Handles tool calls for appointment booking, RAG retrieval, and URL operations
"""

# Fix Windows console encoding for emojis BEFORE any output
import sys
import os
if sys.platform == 'win32':
    try:
        # Set environment variable for UTF-8
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        # Reconfigure stdout/stderr for UTF-8
        import io
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except Exception as e:
        print(f"Warning: Could not set UTF-8 encoding: {e}")

import requests
import json
import re
import contextvars
import time
from urllib.parse import urlparse
from langchain.tools import tool
from pinecone import Pinecone
from pydantic import BaseModel
from langchain.tools import tool
from langchain.tools import StructuredTool
from pydantic.v1 import BaseModel, root_validator
from typing import Dict, Optional
from config import PINECONE_API_KEY, PINECONE_INDEX_NAME, FORM_URL_1, FORM_URL_2, cancelAppointmentFormUrl, rescheduleAppointmentFormUrl
# from chatBot.context import session_id_var
from fastmcp import FastMCP

# Import session-based logging configuration
from logging_config import get_session_logger, SessionLoggerAdapter, log_section_separator, log_dict

# Initialize base logger (will create session-specific loggers later)
print("🚀 MCP Server initializing...")

mcp = FastMCP(name="evaaServer")

# Initialize Pinecone client
pinecone = Pinecone(api_key=PINECONE_API_KEY)
index = pinecone.Index(PINECONE_INDEX_NAME)
print(f"✅ Connected to Pinecone index: {PINECONE_INDEX_NAME}")

# Context variables for session isolation
session_context = contextvars.ContextVar("session_context", default={})

def extract_context_from_message(message: str) -> dict:
    """
    Extract session_id, path, and bot_id from enhanced message format.
    Expected format: [SESSION_ID: xxx] [PATH: xxx] [BOT_ID: xxx] actual_message
    """
    context = {
        'session_id': None,
        'path': None,
        'bot_id': None,
        'clean_message': message
    }
    
    # Extract SESSION_ID
    session_match = re.search(r'\[SESSION_ID:\s*([^\]]+)\]', message)
    if session_match:
        context['session_id'] = session_match.group(1).strip()
    
    # Extract PATH
    path_match = re.search(r'\[PATH:\s*([^\]]+)\]', message)
    if path_match:
        context['path'] = path_match.group(1).strip()
    
    # Extract BOT_ID
    bot_id_match = re.search(r'\[BOT_ID:\s*([^\]]+)\]', message)
    if bot_id_match:
        context['bot_id'] = bot_id_match.group(1).strip()
    
    # Clean the message by removing all context tags
    clean_message = re.sub(r'\[SESSION_ID:[^\]]*\]\s*', '', message)
    clean_message = re.sub(r'\[PATH:[^\]]*\]\s*', '', clean_message)
    clean_message = re.sub(r'\[BOT_ID:[^\]]*\]\s*', '', clean_message)
    context['clean_message'] = clean_message.strip()
    
    return context

def get_bot_id_from_path(path: str) -> str:
    """
    Extract bot_id from path like 'e1/burneteyecarepinecone/QApixW'
    Returns the last part of the path after splitting by '/'
    """
    if not path:
        return ""
    return path.strip('/').split('/')[-1]

def get_session_context(query: str) -> dict:
    """
    Extract and return session context from the query message.
    This ensures thread-safe context extraction for each request.
    """
    context = extract_context_from_message(query)
    
    # If bot_id is not directly provided, try to extract from path
    if not context['bot_id'] and context['path']:
        context['bot_id'] = get_bot_id_from_path(context['path'])
    
    # Store context in thread-local storage
    session_context.set(context)
    
    logger.info(f"📋 Session context extracted | Session: {context.get('session_id', 'N/A')} | Bot: {context.get('bot_id', 'N/A')}")
    log_dict(logger, context, "Session Context")
    return context

def extract_url_from_message(message: str) -> str:
    """
    Extract URL from user message using regex patterns.
    Supports various URL formats and common user expressions.
    """
    # Clean the message from context tags first
    clean_message = re.sub(r'\[SESSION_ID:[^\]]*\]\s*', '', message)
    clean_message = re.sub(r'\[PATH:[^\]]*\]\s*', '', clean_message)
    clean_message = re.sub(r'\[BOT_ID:[^\]]*\]\s*', '', clean_message)
    clean_message = clean_message.strip()
    
    # URL regex patterns (ordered by specificity)
    url_patterns = [
        # Full URLs with protocol
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        # URLs without protocol but with www
        r'www\.[^\s<>"{}|\\^`\[\]]+',
        # Domain-like patterns
        r'[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:/[^\s<>"{}|\\^`\[\]]*)?'
    ]
    
    for pattern in url_patterns:
        matches = re.findall(pattern, clean_message, re.IGNORECASE)
        if matches:
            url = matches[0]
            # Add protocol if missing
            if not url.startswith(('http://', 'https://')):
                if url.startswith('www.'):
                    url = 'https://' + url
                else:
                    # For domain-like patterns, add https://
                    url = 'https://' + url
            return url
    
    return None

def validate_url(url: str) -> dict:
    """
    Validate URL for security and compatibility.
    Returns dict with 'valid' boolean and 'reason' string.
    """
    if not url:
        return {'valid': False, 'reason': 'No URL provided'}
    
    try:
        parsed = urlparse(url)
        
        # Check protocol
        if parsed.scheme not in ['http', 'https']:
            return {'valid': False, 'reason': 'Only HTTP and HTTPS protocols are allowed'}
        
        # Check if domain exists
        if not parsed.netloc:
            return {'valid': False, 'reason': 'Invalid URL format - no domain found'}
        
        # Basic domain validation
        if len(parsed.netloc) < 3 or '.' not in parsed.netloc:
            return {'valid': False, 'reason': 'Invalid domain format'}
        
        # Security checks - block potentially dangerous domains
        dangerous_patterns = [
            r'localhost',
            r'127\.0\.0\.1',
            r'0\.0\.0\.0',
            r'192\.168\.',
            r'10\.',
            r'172\.(1[6-9]|2[0-9]|3[0-1])\.',
            r'file://',
            r'javascript:',
            r'data:'
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                # Allow localhost for development
                if 'localhost' in url and ('3001' in url or '5174' in url):
                    continue
                return {'valid': False, 'reason': 'URL not allowed for security reasons'}
        
        return {'valid': True, 'reason': 'URL is valid'}
        
    except Exception as e:
        return {'valid': False, 'reason': f'URL validation error: {str(e)}'}


class NoInputSchema(BaseModel):
    @root_validator(pre=True)
    def reject_all_inputs(cls, values):
        if values:
            raise ValueError("This tool does not accept any arguments.")
        return values



@mcp.tool()
def book_appointment_tool(query: str = "", session_id: str = None, bot_id: str = None):
    """Use this tool to open appointment booking form so user can fill form and book appointment."""
    try:
        context = {}
        # Extract context from the query if it contains enhanced message format
        if query and ('[SESSION_ID:' in query or '[PATH:' in query or '[BOT_ID:' in query):
            context = get_session_context(query)
            bot_id = context.get('bot_id') or bot_id
            session_id = context.get('session_id') or session_id
        
        # Get session ID from parameter or use fallback
        if not session_id:
            session_id = 'fallback-session-id'
        
        # Create session-specific logger
        logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id), session_id)
        log_section_separator(logger, "BOOK APPOINTMENT TOOL CALLED")
        logger.info(f"🎯 Tool invoked with query: {query[:100]}...")
        logger.info(f"📋 Bot ID: {bot_id} | Session: {session_id}")
        
        # Select form URL based on bot_id
        FORM_URL = FORM_URL_2 if bot_id == "fp01" else FORM_URL_1
        logger.info(f"🔗 Selected form URL for bot '{bot_id}': {FORM_URL}")
        
        # Add session_id and timestamp to form URL
        separator = '&' if '?' in FORM_URL else '?'
        form_url_with_session = f"{FORM_URL}{separator}session_id={session_id}&ts={int(time.time() * 1000)}"
        
        logger.info(f"✅ Generated form URL with session: {form_url_with_session}")
        logger.info("📤 Returning form URL to agent...")
        
        # Return form URL directly in response
        return json.dumps({
            'reply': 'I\'m opening the appointment booking form for you. Please fill it out to book your appointment.',
            'form_url': form_url_with_session,
            'success': True,
            'session_id': session_id
        })
        
    except Exception as e:
        # Create emergency logger if session logger failed
        try:
            logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id if 'session_id' in locals() else 'ERROR'), session_id if 'session_id' in locals() else 'ERROR')
            logger.error(f"❌ Error in book_appointment_tool: {str(e)}")
        except:
            print(f"CRITICAL ERROR in book_appointment_tool: {e}")
        
        return json.dumps({
            'reply': 'Sorry, I couldn\'t open the booking form at this moment.',
            'success': False,
            'session_id': session_id if 'session_id' in locals() else 'unknown'
        })


@mcp.tool()
def cancel_appointment_tool(query: str = "", session_id: str = None, path: str = None, bot_id: str = None):
    """Use this tool to open appointment canceling form so user can fill form and cancel appointment."""
    try:
        # Get session ID from parameter or use fallback FIRST
        if not session_id:
            session_id = 'fallback-session-id'
        
        # Create session-specific logger BEFORE calling get_session_context
        logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id), session_id)
        log_section_separator(logger, "CANCEL APPOINTMENT TOOL CALLED")
        
        context = {}
        # Extract context from the query if it contains enhanced message format
        if query and ('[SESSION_ID:' in query or '[PATH:' in query or '[BOT_ID:' in query):
            context = extract_context_from_message(query)  # Use extract_context instead of get_session_context
            bot_id = context.get('bot_id') or bot_id
            session_id = context.get('session_id') or session_id
        
        logger.info(f"🎯 Tool invoked | Bot ID: {bot_id} | Session: {session_id}")
        logger.info(f"📋 Query: {query[:100]}...")
        
        # Add session_id and timestamp to form URL
        separator = '&' if '?' in cancelAppointmentFormUrl else '?'
        form_url_with_session = f"{cancelAppointmentFormUrl}{separator}session_id={session_id}&ts={int(time.time() * 1000)}"
        
        logger.info(f"✅ Generated form URL: {form_url_with_session}")
        logger.info("📤 Returning form URL to agent...")
        
        # Return form URL directly in response (no API call to form-service)
        return json.dumps({
            'reply': 'I\'m opening the appointment cancellation form for you. Please fill it out to cancel your appointment.',
            'form_url': form_url_with_session,
            'success': True,
            'session_id': session_id
        })
        
    except Exception as e:
        # Create emergency logger if session logger failed
        try:
            logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id if 'session_id' in locals() else 'ERROR'), session_id if 'session_id' in locals() else 'ERROR')
            logger.error(f"❌ Error in cancel_appointment_tool: {str(e)}")
        except:
            print(f"CRITICAL ERROR in cancel_appointment_tool: {e}")
        
        return json.dumps({
            'reply': 'Sorry, I couldn\'t open the appointment cancellation form at this moment.',
            'success': False,
            'session_id': session_id if 'session_id' in locals() else 'unknown'
        })
       

@mcp.tool()
def reschedule_appointment_tool(query: str = "", session_id: str = None, path: str = None, bot_id: str = None):
    """Use this tool to open appointment rescheduling form so user can fill form and reschedule appointment."""
    try:
        # Get session ID from parameter or use fallback FIRST
        if not session_id:
            session_id = 'fallback-session-id'
        
        # Create session-specific logger BEFORE calling get_session_context
        logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id), session_id)
        log_section_separator(logger, "RESCHEDULE APPOINTMENT TOOL CALLED")
        
        context = {}
        # Extract context from the query if it contains enhanced message format
        if query and ('[SESSION_ID:' in query or '[PATH:' in query or '[BOT_ID:' in query):
            context = extract_context_from_message(query)  # Use extract_context instead of get_session_context
            bot_id = context.get('bot_id') or bot_id
            session_id = context.get('session_id') or session_id
        
        logger.info(f"🎯 Tool invoked | Bot ID: {bot_id} | Session: {session_id}")
        logger.info(f"📋 Query: {query[:100]}...")
        
        # Add session_id and timestamp to form URL
        separator = '&' if '?' in rescheduleAppointmentFormUrl else '?'
        form_url_with_session = f"{rescheduleAppointmentFormUrl}{separator}session_id={session_id}&ts={int(time.time() * 1000)}"
        
        logger.info(f"✅ Generated form URL: {form_url_with_session}")
        logger.info("📤 Returning form URL to agent...")
        
        # Return form URL directly in response (no API call to form-service)
        return json.dumps({
            'reply': 'I\'m opening the appointment rescheduling form for you. Please fill it out to reschedule your appointment.',
            'form_url': form_url_with_session,
            'success': True,
            'session_id': session_id
        })
        
    except Exception as e:
        # Create emergency logger if session logger failed
        try:
            logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id if 'session_id' in locals() else 'ERROR'), session_id if 'session_id' in locals() else 'ERROR')
            logger.error(f"❌ Error in reschedule_appointment_tool: {str(e)}")
        except:
            print(f"CRITICAL ERROR in reschedule_appointment_tool: {e}")
        
        return json.dumps({
            'reply': 'Sorry, I couldn\'t open the appointment rescheduling form at this moment.',
            'success': False,
            'session_id': session_id if 'session_id' in locals() else 'unknown'
        })
       

@mcp.tool()
def rag_retrieval_tool(query: str, session_id: str = None, path: str = None, bot_id: str = None) -> str:
    """
    Use This tool to retrieves relevant information from the vector store for every user query/question.
    Now supports dynamic namespace based on bot_id from path.
    """
    try:
        # Extract context from the query if it contains enhanced message format
        context = {}
        if query and ('[SESSION_ID:' in query or '[PATH:' in query or '[BOT_ID:' in query):
            context = get_session_context(query)
            session_id = context.get('session_id') or session_id
            path = context.get('path') or path
            bot_id = context.get('bot_id') or bot_id
            # Use the clean message for the actual query
            query = context.get('clean_message', query)
        
        # Get session ID from parameter or use fallback
        if not session_id:
            session_id = 'fallback-session-id'
        
        # Create session-specific logger
        logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id), session_id)
        log_section_separator(logger, "RAG RETRIEVAL TOOL CALLED")
        logger.info(f"🔍 Query: {query}")
        logger.info(f"📋 Path: {path} | Bot ID: {bot_id}")
        
        # Extract bot_id from path if not provided directly
        if not bot_id and path:
            bot_id = get_bot_id_from_path(path)
            logger.info(f"🏷️ Extracted bot_id from path: {bot_id}")
        
        # Use bot_id as namespace, fallback to default if not available
        namespace = bot_id if bot_id else "hr4s"
        logger.info(f"📂 Using Pinecone namespace: {namespace}")
        
        logger.info(f"🔎 Searching Pinecone with top_k=7, rerank top_n=5...")
        ranked_results = index.search_records(
            namespace=namespace,
            query={
                "inputs": {"text": query},
                "top_k": 7
            },
            rerank={
                "model": "bge-reranker-v2-m3",
                "top_n": 5,
                "rank_fields": ["text"]
            },
        )

        results = ranked_results.result.hits
        if not results:
            logger.warning(f"⚠️ No results found in namespace '{namespace}'")
            return "Sorry, I couldn't find any relevant information."
        
        logger.info(f"✅ Found {len(results)} relevant results")
        logger.info(f"📊 Top result score: {results[0].score if hasattr(results[0], 'score') else 'N/A'}")
        logger.info("📤 Returning results to agent...")
        
        return f"Here's what I found:\n\n{results}"

    except Exception as e:
        # Create emergency logger if session logger failed
        try:
            logger = SessionLoggerAdapter(get_session_logger("MCP Server", session_id if 'session_id' in locals() else 'ERROR'), session_id if 'session_id' in locals() else 'ERROR')
            logger.error(f"❌ Error in RAG retrieval: {str(e)}")
            logger.error(f"🔍 Failed query: {query[:100] if 'query' in locals() else 'N/A'}")
        except:
            print(f"CRITICAL ERROR in rag_retrieval_tool: {e}")
        
        return f"Error retrieving context from Pinecone: {e}"




if __name__ == "__main__":
    # mcp.run(transport="sse", port=8005)
    mcp.run(transport="stdio")
    # mcp.run(transport="streamable-http", port=8005)



