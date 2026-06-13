import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    # 1. Create logs directory in the backend directory
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(backend_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    app_log = os.path.join(log_dir, "app.log")
    error_log = os.path.join(log_dir, "error.log")
    
    # 2. Define custom formatter
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] - %(message)s'
    )
    
    # 3. Create app log handler (all logs from INFO up)
    app_handler = RotatingFileHandler(app_log, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)
    
    # 4. Create error log handler (ERROR and CRITICAL logs only)
    error_handler = RotatingFileHandler(error_log, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    
    # 5. Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove any existing console/stream handlers from the root logger
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)
    
    # 6. Configure Uvicorn, SQLAlchemy, and WeasyPrint loggers to redirect to file only
    loggers_to_redirect = [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "sqlalchemy.engine",
        "email_utils",
        "pdf_generator",
        "main",
        "weasyprint",
        "weasyprint.progress",
        "fontconfig"
    ]
    
    for logger_name in loggers_to_redirect:
        logger = logging.getLogger(logger_name)
        # Suppress verbose info progress statements from weasyprint and fontconfig
        if "weasyprint" in logger_name or logger_name == "fontconfig":
            logger.setLevel(logging.WARNING)
        else:
            logger.setLevel(logging.INFO)
            
        logger.propagate = False  # Avoid duplicating logs in the root logger
        
        # Clear existing handlers (especially console StreamHandlers)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            
        # Add our file-based handlers
        logger.addHandler(app_handler)
        logger.addHandler(error_handler)


# Run setup on import
setup_logging()
