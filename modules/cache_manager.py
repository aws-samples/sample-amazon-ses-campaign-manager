#!/usr/bin/env python3
"""
Cache Manager Module
Handles caching functionality for API responses to improve performance and reduce AWS API calls
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
import hashlib

from config.settings import PROJECT_ROOT
from modules.logger import get_logger


class CacheManager:
    """Manages caching of API responses with configurable TTL and automatic expiration."""
    
    def __init__(self, settings, namespace = None, cache_dir = None):
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir else PROJECT_ROOT / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        # Namespace (e.g. "<profile>_<region>") keeps data from different accounts/regions apart
        self.namespace = re.sub(r'[^\w.-]', '_', namespace) if namespace else None
        
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
        """Generate a unique cache key for an operation, its parameters and the namespace."""
        cache_key = operation
        if params:
            # Create a consistent hash of the parameters
            params_str = json.dumps(params, sort_keys=True)
            params_hash = hashlib.md5(params_str.encode()).hexdigest()[:8]  # nosec B324 - MD5 used for cache keys, not security
            cache_key = f"{operation}_{params_hash}"
        if self.namespace:
            cache_key = f"{self.namespace}__{cache_key}"
        return cache_key
    
    def _get_cache_file_path(self, cache_key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{cache_key}.json"
    
    def _read_cache_file(self, operation: str, params = None):
        """Read a cache entry regardless of expiry. Removes corrupted files."""
        cache_file = self._get_cache_file_path(self._get_cache_key(operation, params))
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
            self._log_debug(f"Cache ERROR - corrupted cache file {cache_file}: {e}")
            cache_file.unlink(missing_ok=True)
            return None
    
    def _is_cache_valid(self, cache_data, operation: str = None) -> bool:
        """Check if cached data is still valid based on the expiry stored with it."""
        # Entries written before expiry was stored with the data count as expired
        if not cache_data or 'expires_at' not in cache_data:
            return False
        
        try:
            expiry_time = datetime.fromisoformat(cache_data['expires_at'])
            is_valid = datetime.now() < expiry_time
            
            if self.debug_logging:
                time_left = expiry_time - datetime.now()
                operation_info = f" for '{operation}'" if operation else ""
                self._log_debug(f"Cache validity check{operation_info}: {'Valid' if is_valid else 'Expired'}, Time left: {time_left}")
            
            return is_valid
        except (ValueError, TypeError) as e:
            operation_info = f" for '{operation}'" if operation else ""
            self._log_debug(f"Error parsing cache expiry{operation_info}: {e}")
            return False
    
    def get_cached_data(self, operation: str, params = None):
        """Get cached data for an operation if it exists and is valid."""
        if not self.cache_enabled:
            self._log_debug(f"Cache disabled, skipping get for operation: {operation}")
            return None
        
        self._log_debug(f"Checking cache for operation: {operation}, key: {self._get_cache_key(operation, params)}")
        
        cache_data = self._read_cache_file(operation, params)
        if cache_data is None:
            self._log_debug(f"Cache MISS - no cache entry for operation: {operation}")
            return None
        
        if self._is_cache_valid(cache_data, operation):
            self._log_debug(f"Cache HIT - returning cached data for operation: {operation}")
            return cache_data.get('data')
        
        # Expired entries are kept as a fallback for failed API calls; cleanup_expired_cache removes them
        self._log_debug(f"Cache EXPIRED for operation: {operation}")
        return None
    
    def get_stale_data(self, operation: str, params = None):
        """Get cached data for an operation even if it has expired."""
        if not self.cache_enabled:
            return None
        
        cache_data = self._read_cache_file(operation, params)
        return cache_data.get('data') if cache_data else None
    
    def set_cached_data(self, operation: str, data, params = None, ttl_minutes = None) -> None:
        """Cache data for an operation."""
        if not self.cache_enabled:
            self._log_debug(f"Cache disabled, skipping set for operation: {operation}")
            return
        
        cache_key = self._get_cache_key(operation, params)
        cache_file = self._get_cache_file_path(cache_key)
        
        # Store the expiry with the entry so every reader and cleanup applies the same TTL
        ttl = ttl_minutes if ttl_minutes is not None else self.default_ttl_minutes
        now = datetime.now()
        
        cache_data = {
            'timestamp': now.isoformat(),
            'expires_at': (now + timedelta(minutes=ttl)).isoformat(),
            'operation': operation,
            'namespace': self.namespace,
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
                    cache_file.unlink(missing_ok=True)
                    removed_count += 1
            
            if removed_count > 0:
                self._log_debug(f"Cleaned up {removed_count} expired cache files")
        
        except Exception as e:
            self._log_debug(f"Error during cache cleanup: {e}")
        
        return removed_count


class CachedAPIWrapper:
    """Wrapper class to add caching functionality to API operations."""
    
    def __init__(self, cache_manager: CacheManager):
        self.cache_manager = cache_manager
    
    def cached_call(self,
                   operation_name: str,
                   api_function,
                   params = None,
                   ttl_minutes = None,
                   force_refresh: bool = False,
                   cache_if = None):
        """
        Execute an API call with caching support.
        
        Args:
            operation_name: Unique name for the operation
            api_function: The API function to call
            params: Parameters for the API function
            ttl_minutes: Cache TTL in minutes (uses default if None)
            force_refresh: If True, bypass cache and force API call
            cache_if: Optional check on the result; the result is only cached when it returns True
        
        Returns:
            The API response data
        """
        params = params or {}
        
        # Check cache first unless force refresh is requested
        if not force_refresh:
            cached_data = self.cache_manager.get_cached_data(operation_name, params)
            if cached_data is not None:
                self.cache_manager._log_debug(f"API WRAPPER: Returning cached data for {operation_name}")
                return cached_data
        
        # Cache miss or force refresh - call the API
        self.cache_manager._log_debug(f"API WRAPPER: Fetching fresh data from SES for {operation_name}")
        try:
            result = api_function(**params)
        except Exception:
            # If API call fails, return expired data for the same account/region as a fallback
            if not force_refresh:
                stale_data = self.cache_manager.get_stale_data(operation_name, params)
                if stale_data is not None:
                    self.cache_manager._log_debug(f"API call failed, returning stale cache data for {operation_name}")
                    return stale_data
            raise
        
        # Don't cache partial or error results
        if cache_if is None or cache_if(result):
            self.cache_manager.set_cached_data(operation_name, result, params, ttl_minutes)
            self.cache_manager._log_debug(f"API WRAPPER: Successfully fetched and cached data for {operation_name}")
        else:
            self.cache_manager._log_debug(f"API WRAPPER: Result for {operation_name} not cached (incomplete)")
        
        return result
