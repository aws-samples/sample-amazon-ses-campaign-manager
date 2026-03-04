#!/usr/bin/env python3
"""
Cache Manager Module
Handles caching functionality for API responses to improve performance and reduce AWS API calls
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import hashlib

from config.settings import Settings
from modules.logger import get_logger


class CacheManager:
    """Manages caching of API responses with configurable TTL and automatic expiration."""
    
    def __init__(self, settings, cache_dir: str = "cache"):
        self.settings = settings
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        # Default cache TTL in minutes (can be overridden in settings)
        self.default_ttl_minutes = self.settings.get('cache.default_ttl_minutes', 15)
        
        # Cache enabled flag
        self.cache_enabled = self.settings.get('cache.enabled', True)
        
        # Debug logging
        self.debug_logging = self.settings.get('app.debug_logging', False)
    
    def _log_debug(self, message: str) -> None:
        """Log debug message using centralized logger."""
        logger = get_logger()
        if logger:
            logger.cache_operation(message)
    
    def _get_cache_key(self, operation: str, params = None) -> str:
        """Generate a unique cache key for an operation and its parameters."""
        if params:
            # Create a consistent hash of the parameters
            params_str = json.dumps(params, sort_keys=True)
            params_hash = hashlib.md5(params_str.encode()).hexdigest()[:8]  # nosec B324 - MD5 used for cache keys, not security
            return f"{operation}_{params_hash}"
        return operation
    
    def _get_cache_file_path(self, cache_key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{cache_key}.json"
    
    def _is_cache_valid(self, cache_data, ttl_minutes = None, operation: str = None) -> bool:
        """Check if cached data is still valid based on TTL."""
        if not cache_data or 'timestamp' not in cache_data:
            return False
        
        # Use provided TTL or default
        ttl = ttl_minutes if ttl_minutes is not None else self.default_ttl_minutes
        
        try:
            cached_time = datetime.fromisoformat(cache_data['timestamp'])
            expiry_time = cached_time + timedelta(minutes=ttl)
            is_valid = datetime.now() < expiry_time
            
            if self.debug_logging:
                time_left = expiry_time - datetime.now()
                operation_info = f" for '{operation}'" if operation else ""
                self._log_debug(f"Cache validity check{operation_info}: {'Valid' if is_valid else 'Expired'}, Time left: {time_left}")
            
            return is_valid
        except (ValueError, TypeError) as e:
            operation_info = f" for '{operation}'" if operation else ""
            self._log_debug(f"Error parsing cache timestamp{operation_info}: {e}")
            return False
    
    def _auto_cleanup_expired(self) -> None:
        """Automatically cleanup expired cache files periodically."""
        # Only run cleanup every 10th call to avoid performance impact
        if not hasattr(self, '_cleanup_counter'):
            self._cleanup_counter = 0
        
        self._cleanup_counter += 1
        if self._cleanup_counter % 10 == 0:
            removed_count = self.cleanup_expired_cache()
            if removed_count > 0:
                self._log_debug(f"Auto-cleanup removed {removed_count} expired cache files")
    
    def get_cached_data(self, operation: str, params = None, ttl_minutes = None):
        """Get cached data for an operation if it exists and is valid."""
        if not self.cache_enabled:
            self._log_debug(f"Cache disabled, skipping get for operation: {operation}")
            return None
        
        # Automatically cleanup expired cache files periodically
        self._auto_cleanup_expired()
        
        cache_key = self._get_cache_key(operation, params)
        cache_file = self._get_cache_file_path(cache_key)
        
        self._log_debug(f"Checking cache for operation: {operation}, key: {cache_key}")
        
        if not cache_file.exists():
            self._log_debug(f"Cache MISS - file does not exist for operation: {operation}")
            return None
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            if self._is_cache_valid(cache_data, ttl_minutes, operation):
                self._log_debug(f"Cache HIT - returning cached data for operation: {operation}")
                return cache_data.get('data')
            else:
                self._log_debug(f"Cache EXPIRED - removing expired cache for operation: {operation}")
                # Remove expired cache file
                cache_file.unlink()
                return None
                
        except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
            self._log_debug(f"Cache ERROR - corrupted cache file {cache_file}: {e}")
            # Remove corrupted cache file
            if cache_file.exists():
                cache_file.unlink()
            return None
    
    def set_cached_data(self, operation: str, data, params = None) -> None:
        """Cache data for an operation."""
        if not self.cache_enabled:
            self._log_debug(f"Cache disabled, skipping set for operation: {operation}")
            return
        
        cache_key = self._get_cache_key(operation, params)
        cache_file = self._get_cache_file_path(cache_key)
        
        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'operation': operation,
            'params': params,
            'data': data
        }
        
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, default=str)
            
            self._log_debug(f"Cached data for operation: {operation}, key: {cache_key}")
            
        except Exception as e:
            self._log_debug(f"Error writing cache file {cache_file}: {e}")
    
    def invalidate_cache(self, operation: str, params = None) -> None:
        """Invalidate cached data for a specific operation."""
        cache_key = self._get_cache_key(operation, params)
        cache_file = self._get_cache_file_path(cache_key)
        
        if cache_file.exists():
            cache_file.unlink()
            self._log_debug(f"Invalidated cache for operation: {operation}, key: {cache_key}")
    
    def invalidate_all_cache(self) -> None:
        """Invalidate all cached data."""
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
            self._log_debug("Invalidated all cache files")
        except Exception as e:
            self._log_debug(f"Error invalidating all cache: {e}")
    
    def get_cache_stats(self):
        """Get cache statistics."""
        stats = {
            'enabled': self.cache_enabled,
            'default_ttl_minutes': self.default_ttl_minutes,
            'total_cache_files': 0,
            'valid_cache_files': 0,
            'expired_cache_files': 0,
            'cache_size_mb': 0
        }
        
        try:
            cache_files = list(self.cache_dir.glob("*.json"))
            stats['total_cache_files'] = len(cache_files)
            
            total_size = 0
            for cache_file in cache_files:
                try:
                    file_size = cache_file.stat().st_size
                    total_size += file_size
                    
                    # Check if cache is valid
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    
                    if self._is_cache_valid(cache_data, operation=cache_data.get('operation', 'unknown')):
                        stats['valid_cache_files'] += 1
                    else:
                        stats['expired_cache_files'] += 1
                        
                except Exception:
                    stats['expired_cache_files'] += 1
            
            stats['cache_size_mb'] = round(total_size / (1024 * 1024), 2)
            
        except Exception as e:
            self._log_debug(f"Error getting cache stats: {e}")
        
        return stats
    
    def cleanup_expired_cache(self) -> int:
        """Clean up expired cache files and return the number of files removed."""
        removed_count = 0
        
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    
                    if not self._is_cache_valid(cache_data, operation=cache_data.get('operation', 'unknown')):
                        cache_file.unlink()
                        removed_count += 1
                        
                except Exception:
                    # Remove corrupted cache files
                    cache_file.unlink()
                    removed_count += 1
            
            if removed_count > 0:
                self._log_debug(f"Cleaned up {removed_count} expired cache files")
                
        except Exception as e:
            self._log_debug(f"Error during cache cleanup: {e}")
        
        return removed_count
    
    def update_settings(self, settings: Settings) -> None:
        """Update cache manager settings."""
        self.settings = settings
        self.default_ttl_minutes = self.settings.get('cache.default_ttl_minutes', 15)
        self.cache_enabled = self.settings.get('cache.enabled', True)
        self.debug_logging = self.settings.get('app.debug_logging', False)
        
        self._log_debug(f"Cache settings updated - Enabled: {self.cache_enabled}, TTL: {self.default_ttl_minutes} minutes")


class CachedAPIWrapper:
    """Wrapper class to add caching functionality to API operations."""
    
    def __init__(self, cache_manager: CacheManager):
        self.cache_manager = cache_manager
    
    def cached_call(self, 
                   operation_name: str, 
                   api_function, 
                   params = None,
                   ttl_minutes = None,
                   force_refresh: bool = False):
        """
        Execute an API call with caching support.
        
        Args:
            operation_name: Unique name for the operation
            api_function: The API function to call
            params: Parameters for the API function
            ttl_minutes: Cache TTL in minutes (uses default if None)
            force_refresh: If True, bypass cache and force API call
        
        Returns:
            The API response data
        """
        params = params or {}
        
        # Check cache first unless force refresh is requested
        if not force_refresh:
            cached_data = self.cache_manager.get_cached_data(operation_name, params, ttl_minutes)
            if cached_data is not None:
                self.cache_manager._log_debug(f"API WRAPPER: Returning cached data for {operation_name}")
                return cached_data
        
        # Cache miss or force refresh - call the API
        self.cache_manager._log_debug(f"API WRAPPER: Fetching fresh data from SES for {operation_name}")
        try:
            if params:
                # Call API function with parameters
                result = api_function(**params)
            else:
                # Call API function without parameters
                result = api_function()
            
            # Cache the result
            self.cache_manager.set_cached_data(operation_name, result, params)
            self.cache_manager._log_debug(f"API WRAPPER: Successfully fetched and cached data for {operation_name}")
            
            return result
            
        except Exception as e:
            # If API call fails, try to return stale cache data as fallback
            if not force_refresh:
                # Look for any cached data, even if expired
                cache_key = self.cache_manager._get_cache_key(operation_name, params)
                cache_file = self.cache_manager._get_cache_file_path(cache_key)
                
                if cache_file.exists():
                    try:
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            cache_data = json.load(f)
                        
                        self.cache_manager._log_debug(f"API call failed, returning stale cache data for {operation_name}")
                        return cache_data.get('data')
                    except Exception:
                        pass  # nosec B110 - Intentional - widget may not be mounted
            
            # Re-raise the original exception if no fallback available
            raise e
