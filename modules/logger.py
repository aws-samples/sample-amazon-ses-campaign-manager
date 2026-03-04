#!/usr/bin/env python3
"""
Centralized Logger Module
Provides centralized debug logging functionality across all modules
"""

import os
from datetime import datetime
from pathlib import Path


class Logger:
    """Centralized logger that handles both console and file logging based on debug settings."""
    
    def __init__(self, settings):
        self.settings = settings
        # Use logs directory
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(exist_ok=True)
        self.log_file_path = self.logs_dir / "debug.log"
        self._ensure_log_file()
    
    def _get_max_log_size_mb(self) -> float:
        """Get maximum log file size in MB from settings."""
        return self.settings.get('app.max_log_size_mb', 5.0)
    
    def _get_max_backup_logs(self) -> int:
        """Get maximum number of backup log files to keep (default 5)."""
        return self.settings.get('app.max_backup_logs', 5)
    
    def _cleanup_old_backups(self) -> int:
        """
        Remove old backup log files beyond the configured limit.
        Returns number of files deleted.
        """
        try:
            max_backups = self._get_max_backup_logs()
            backup_files = []
            
            # Get all backup files with their timestamps
            for file in self.logs_dir.glob("debug_*.log"):
                backup_files.append({
                    'path': file,
                    'mtime': file.stat().st_mtime
                })
            
            # Sort by modification time, newest first
            backup_files.sort(key=lambda x: x['mtime'], reverse=True)
            
            # Delete files beyond the limit
            files_to_delete = backup_files[max_backups:]
            deleted_count = 0
            for file_info in files_to_delete:
                file_info['path'].unlink()
                deleted_count += 1
            
            return deleted_count
                
        except Exception as e:
            # If cleanup fails, just continue (better than crashing)
            print(f"Warning: Backup cleanup failed: {e}")
            return 0
    
    def _check_and_rotate_log(self) -> None:
        """Check log file size and rotate if needed."""
        try:
            if not self.log_file_path.exists():
                return
            
            # Get current file size in MB
            current_size_mb = self.get_log_file_size()
            max_size_mb = self._get_max_log_size_mb()
            
            # If file exceeds limit, rotate it
            if current_size_mb >= max_size_mb:
                # Rename current log to backup in logs directory
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = self.logs_dir / f"debug_{timestamp}.log"
                
                # Move current log to backup
                self.log_file_path.rename(backup_path)
                
                # Clean up old backup files beyond the limit
                deleted_count = self._cleanup_old_backups()
                
                # Create new empty log file
                self.log_file_path.touch()
                
                # Write rotation notice to new file
                max_backups = self._get_max_backup_logs()
                with open(self.log_file_path, 'a', encoding='utf-8') as f:
                    f.write(f"[{self._get_timestamp()}] INFO: Log rotated. Previous log saved to {backup_path.name}\n")
                    f.write(f"[{self._get_timestamp()}] INFO: Max log size: {max_size_mb} MB | Keeping {max_backups} backups\n")
                    if deleted_count > 0:
                        f.write(f"[{self._get_timestamp()}] INFO: Deleted {deleted_count} old backup file(s) to maintain limit\n")
                    f.write("\n")
        except Exception as e:
            # If rotation fails, just continue (better than crashing)
            print(f"Warning: Log rotation failed: {e}")
    
    def _ensure_log_file(self) -> None:
        """Ensure the log file exists."""
        if not self.log_file_path.exists():
            self.log_file_path.touch()
    
    def _get_timestamp(self) -> str:
        """Get formatted timestamp for logging."""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    def _is_debug_enabled(self) -> bool:
        """Check if debug logging is enabled."""
        return self.settings.get('app.debug_logging', False)
    
    def _write_to_file(self, level: str, message: str, module: str = None) -> None:
        """Write log entry to file."""
        try:
            # Check and rotate log if needed before writing
            self._check_and_rotate_log()
            
            timestamp = self._get_timestamp()
            module_prefix = f"[{module}] " if module else ""
            log_entry = f"[{timestamp}] {level}: {module_prefix}{message}\n"
            
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            # Fallback to console if file writing fails
            print(f"Failed to write to log file: {e}")
    
    def info(self, message: str, module: str = None) -> None:
        """Log informational message (always shown)."""
        timestamp = self._get_timestamp()
        module_prefix = f"[{module}] " if module else ""
        console_message = f"[{timestamp}] INFO: {module_prefix}{message}"
        
        # Always show informational messages
        print(console_message)
        
        # Write to file if debug is enabled
        if self._is_debug_enabled():
            self._write_to_file("INFO", message, module)
    
    def debug(self, message: str, module: str = None) -> None:
        """Log debug message (only shown when debug is enabled)."""
        if self._is_debug_enabled():
            timestamp = self._get_timestamp()
            module_prefix = f"[{module}] " if module else ""
            console_message = f"[{timestamp}] DEBUG: {module_prefix}{message}"
            
            # Show on console
            print(console_message)
            
            # Write to file
            self._write_to_file("DEBUG", message, module)
    
    def warning(self, message: str, module: str = None) -> None:
        """Log warning message (always shown)."""
        timestamp = self._get_timestamp()
        module_prefix = f"[{module}] " if module else ""
        console_message = f"[{timestamp}] WARNING: {module_prefix}{message}"
        
        # Always show warnings
        print(console_message)
        
        # Write to file if debug is enabled
        if self._is_debug_enabled():
            self._write_to_file("WARNING", message, module)
    
    def error(self, message: str, module: str = None) -> None:
        """Log error message (always shown)."""
        timestamp = self._get_timestamp()
        module_prefix = f"[{module}] " if module else ""
        console_message = f"[{timestamp}] ERROR: {module_prefix}{message}"
        
        # Always show errors
        print(console_message)
        
        # Write to file if debug is enabled
        if self._is_debug_enabled():
            self._write_to_file("ERROR", message, module)
    
    def success(self, message: str, module: str = None) -> None:
        """Log success message (always shown)."""
        timestamp = self._get_timestamp()
        module_prefix = f"[{module}] " if module else ""
        console_message = f"[{timestamp}] SUCCESS: {module_prefix}{message}"
        
        # Always show success messages
        print(console_message)
        
        # Write to file if debug is enabled
        if self._is_debug_enabled():
            self._write_to_file("SUCCESS", message, module)
    
    def cache_operation(self, message: str, module: str = "CACHE") -> None:
        """Log cache operation (debug level)."""
        self.debug(message, module)
    
    def api_operation(self, message: str, module: str = "API") -> None:
        """Log API operation (debug level)."""
        self.debug(message, module)
    
    def ui_operation(self, message: str, module: str = "UI") -> None:
        """Log UI operation (debug level)."""
        self.debug(message, module)
    
    def clear_log_file(self) -> None:
        """Clear the debug log file."""
        try:
            with open(self.log_file_path, 'w', encoding='utf-8') as f:
                f.write("")
            self.info("Debug log file cleared", "LOGGER")
        except Exception as e:
            self.error(f"Failed to clear log file: {e}", "LOGGER")
    
    def get_log_file_path(self) -> str:
        """Get the path to the log file."""
        return str(self.log_file_path.absolute())
    
    def get_log_file_size(self) -> float:
        """Get log file size in MB."""
        try:
            if self.log_file_path.exists():
                size_bytes = self.log_file_path.stat().st_size
                return round(size_bytes / (1024 * 1024), 2)
            return 0.0
        except Exception:
            return 0.0
    
    def get_backup_log_files(self) -> list:
        """Get list of backup log files from logs directory."""
        try:
            backup_files = []
            for file in self.logs_dir.glob("debug_*.log"):
                # Exclude the current debug.log file, only show backups
                if file.name != "debug.log":
                    backup_files.append({
                        'name': file.name,
                        'size_mb': round(file.stat().st_size / (1024 * 1024), 2),
                        'modified': datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
            # Sort by modification time, newest first
            backup_files.sort(key=lambda x: x['modified'], reverse=True)
            return backup_files
        except Exception:
            return []


# Global logger instance (will be initialized by the main app)
logger = None


def init_logger(settings):
    """Initialize the global logger instance."""
    global logger
    logger = Logger(settings)
    return logger


def get_logger():
    """Get the global logger instance."""
    return logger
