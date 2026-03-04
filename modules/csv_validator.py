"""
CSV Validator Module for Bulk Email Sender

This module provides validation functionality for CSV files used in bulk email campaigns.
It validates file structure, required columns, email formats, and data integrity.
"""

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of CSV validation"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    row_count: int
    valid_row_count: int
    
    def get_summary(self) -> str:
        """Get a human-readable summary of validation results"""
        if self.is_valid:
            summary = f"✓ Validation passed: {self.valid_row_count} valid recipients"
            if self.warnings:
                summary += f"\n⚠ {len(self.warnings)} warning(s)"
        else:
            summary = f"✗ Validation failed: {len(self.errors)} error(s)"
        return summary


class CSVValidator:
    """Validates CSV files for bulk email sending"""
    
    # Email regex pattern (basic validation)
    EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    
    # Required columns
    REQUIRED_COLUMNS = ['To_Address']
    
    # Optional column prefix for substitution variables
    SUBSTITUTION_PREFIX = 'sub_'
    
    # System-managed variables that should be excluded from validation
    SYSTEM_VARIABLES = {
        'unsubscribe_link', 'unsubscribe_url', 'unsubscribe',
        'preferences_url', 'preference_center', 'preferences_link'
    }
    
    # Limits
    MAX_FILE_SIZE_MB = 50
    MAX_ROWS = 50000
    
    def __init__(self, logger=None, ses_client=None):
        """
        Initialize CSV validator
        
        Args:
            logger: Optional logger instance for logging validation messages
            ses_client: Optional SES client for template variable validation
        """
        self.logger = logger
        self.ses_client = ses_client
    
    def validate_csv_file(
        self,
        csv_path: str,
        check_duplicates: bool = True,
        max_rows: Optional[int] = None,
        template_name: Optional[str] = None
    ) -> ValidationResult:
        """
        Validate a CSV file for bulk email sending
        
        Args:
            csv_path: Path to the CSV file
            check_duplicates: Whether to check for duplicate email addresses
            max_rows: Maximum number of rows allowed (None for default)
            template_name: Optional template name to validate CSV columns against template variables
            
        Returns:
            ValidationResult object with validation details
        """
        errors = []
        warnings = []
        row_count = 0
        valid_row_count = 0
        
        # Use default max_rows if not specified
        if max_rows is None:
            max_rows = self.MAX_ROWS
        
        try:
            # Step 1: File existence and basic checks
            file_errors = self._validate_file_existence(csv_path)
            if file_errors:
                errors.extend(file_errors)
                return ValidationResult(False, errors, warnings, 0, 0)
            
            csv_file = Path(csv_path)
            
            # Step 2: File size check
            size_errors = self._validate_file_size(csv_file)
            if size_errors:
                errors.extend(size_errors)
                return ValidationResult(False, errors, warnings, 0, 0)
            
            # Step 3: Parse and validate CSV structure
            with open(csv_file, 'r', encoding='utf-8') as f:
                try:
                    reader = csv.DictReader(f)
                    
                    # Step 4: Validate headers
                    header_errors, header_warnings = self._validate_headers(reader.fieldnames)
                    errors.extend(header_errors)
                    warnings.extend(header_warnings)
                    
                    if header_errors:
                        return ValidationResult(False, errors, warnings, 0, 0)
                    
                    # Step 4.5: Validate template variables against CSV columns (if template provided)
                    if template_name and self.ses_client:
                        template_errors = self._validate_template_variables(
                            template_name, reader.fieldnames
                        )
                        if template_errors:
                            errors.extend(template_errors)
                            return ValidationResult(False, errors, warnings, 0, 0)
                    
                    # Step 5: Validate rows
                    seen_emails = set() if check_duplicates else None
                    
                    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
                        row_count += 1
                        
                        # Check max rows limit
                        if row_count > max_rows:
                            errors.append(f"File exceeds maximum allowed rows ({max_rows})")
                            break
                        
                        # Validate individual row
                        row_errors, row_warnings = self._validate_row(
                            row, row_num, seen_emails
                        )
                        
                        if row_errors:
                            errors.extend(row_errors)
                        else:
                            valid_row_count += 1
                        
                        if row_warnings:
                            warnings.extend(row_warnings)
                    
                    # Step 6: Check if file is empty
                    if row_count == 0:
                        errors.append("CSV file is empty (no data rows)")
                    elif valid_row_count == 0:
                        errors.append("No valid recipients found in CSV file")
                
                except csv.Error as e:
                    errors.append(f"CSV parsing error: {str(e)}")
                    return ValidationResult(False, errors, warnings, row_count, valid_row_count)
        
        except Exception as e:
            errors.append(f"Unexpected error during validation: {str(e)}")
            return ValidationResult(False, errors, warnings, row_count, valid_row_count)
        
        # Determine if validation passed
        is_valid = len(errors) == 0 and valid_row_count > 0
        
        # Log results
        if self.logger:
            if is_valid:
                self.logger.info(
                    f"CSV validation passed: {valid_row_count}/{row_count} valid rows",
                    "CSV_VALIDATOR"
                )
                if warnings:
                    for warning in warnings:
                        self.logger.warning(warning, "CSV_VALIDATOR")
            else:
                self.logger.error(
                    f"CSV validation failed with {len(errors)} error(s)",
                    "CSV_VALIDATOR"
                )
                for error in errors[:5]:  # Log first 5 errors
                    self.logger.error(error, "CSV_VALIDATOR")
        
        return ValidationResult(is_valid, errors, warnings, row_count, valid_row_count)
    
    def _validate_template_variables(
        self,
        template_name: str,
        csv_headers: List[str]
    ) -> List[str]:
        """
        Validate that CSV substitution columns match template variables.
        
        This ensures that:
        1. All template variables (except system variables) have corresponding CSV columns
        2. All CSV substitution columns have corresponding template variables
        3. No mismatches that would cause template rendering failures
        
        Args:
            template_name: Name of the email template
            csv_headers: List of CSV column headers
            
        Returns:
            List of error messages (empty if validation passes)
        """
        errors = []
        
        try:
            # Extract template placeholders
            template_vars = self.ses_client.extract_template_placeholders(template_name)
            
            if not template_vars:
                # No variables in template, no validation needed
                return errors
            
            # Filter out system-managed variables (unsubscribe links, etc.)
            template_vars_filtered = {
                k: v for k, v in template_vars.items() 
                if k.lower() not in self.SYSTEM_VARIABLES
            }
            
            # Extract CSV substitution columns (remove 'sub_' prefix for comparison)
            csv_sub_columns = [
                col[len(self.SUBSTITUTION_PREFIX):] 
                for col in csv_headers 
                if col.startswith(self.SUBSTITUTION_PREFIX)
            ]
            
            # Convert to sets for comparison (case-insensitive)
            template_var_set = {var.lower() for var in template_vars_filtered.keys()}
            csv_sub_set = {col.lower() for col in csv_sub_columns}
            
            # Check for template variables missing in CSV
            missing_in_csv = template_var_set - csv_sub_set
            if missing_in_csv:
                errors.append(
                    f"Template '{template_name}' requires variables that are missing in CSV: "
                    f"{', '.join(sorted(missing_in_csv))}. "
                    f"Add columns: {', '.join(['sub_' + var for var in sorted(missing_in_csv)])}"
                )
            
            # Check for CSV columns not used in template
            extra_in_csv = csv_sub_set - template_var_set
            if extra_in_csv:
                errors.append(
                    f"CSV contains substitution columns not used in template '{template_name}': "
                    f"{', '.join(['sub_' + var for var in sorted(extra_in_csv)])}. "
                    f"These columns will be ignored during email sending."
                )
            
            # Log validation details
            if self.logger and not errors:
                self.logger.info(
                    f"Template variable validation passed: {len(template_var_set)} variables matched",
                    "CSV_VALIDATOR"
                )
            
        except Exception as e:
            errors.append(
                f"Failed to validate template variables: {str(e)}. "
                f"Ensure template '{template_name}' exists and is accessible."
            )
        
        return errors
    
    def _validate_file_existence(self, csv_path: str) -> List[str]:
        """Validate that file exists and is readable"""
        errors = []
        
        if not csv_path:
            errors.append("CSV file path is empty")
            return errors
        
        csv_file = Path(csv_path)
        
        if not csv_file.exists():
            errors.append(f"CSV file not found: {csv_path}")
        elif not csv_file.is_file():
            errors.append(f"Path is not a file: {csv_path}")
        elif not csv_file.suffix.lower() == '.csv':
            errors.append(f"File must have .csv extension: {csv_path}")
        
        return errors
    
    def _validate_file_size(self, csv_file: Path) -> List[str]:
        """Validate file size is within limits"""
        errors = []
        
        file_size_mb = csv_file.stat().st_size / (1024 * 1024)
        if file_size_mb > self.MAX_FILE_SIZE_MB:
            errors.append(
                f"File size ({file_size_mb:.2f} MB) exceeds maximum "
                f"allowed size ({self.MAX_FILE_SIZE_MB} MB)"
            )
        
        return errors
    
    def _validate_headers(
        self,
        fieldnames: Optional[List[str]]
    ) -> Tuple[List[str], List[str]]:
        """Validate CSV headers"""
        errors = []
        warnings = []
        
        if not fieldnames:
            errors.append("CSV file has no headers")
            return errors, warnings
        
        # Check for required columns
        for required_col in self.REQUIRED_COLUMNS:
            if required_col not in fieldnames:
                errors.append(f"Missing required column: '{required_col}'")
        
        # Check for empty column names
        if '' in fieldnames or None in fieldnames:
            warnings.append("CSV contains empty column names")
        
        # Check for duplicate column names
        if len(fieldnames) != len(set(fieldnames)):
            duplicates = [col for col in fieldnames if fieldnames.count(col) > 1]
            warnings.append(f"Duplicate column names found: {', '.join(set(duplicates))}")
        
        # Check for substitution columns
        sub_columns = [col for col in fieldnames if col.startswith(self.SUBSTITUTION_PREFIX)]
        if not sub_columns:
            warnings.append(
                "No substitution columns found (columns starting with 'sub_'). "
                "Template variables will not be replaced."
            )
        
        return errors, warnings
    
    def _validate_row(
        self,
        row: Dict[str, str],
        row_num: int,
        seen_emails: Optional[set]
    ) -> Tuple[List[str], List[str]]:
        """Validate a single CSV row"""
        errors = []
        warnings = []
        
        # Get email address
        to_address = row.get('To_Address', '').strip()
        
        # Check if email is empty
        if not to_address:
            errors.append(f"Row {row_num}: Missing email address in 'To_Address' column")
            return errors, warnings
        
        # Validate email format
        if not self._is_valid_email(to_address):
            errors.append(f"Row {row_num}: Invalid email format: '{to_address}'")
        
        # Check for duplicates
        if seen_emails is not None:
            if to_address.lower() in seen_emails:
                warnings.append(f"Row {row_num}: Duplicate email address: '{to_address}'")
            else:
                seen_emails.add(to_address.lower())
        
        # Check substitution variables - EMPTY VALUES ARE ERRORS
        sub_vars = {k: v for k, v in row.items() if k.startswith(self.SUBSTITUTION_PREFIX)}
        for key, value in sub_vars.items():
            if value is None or value.strip() == '':
                errors.append(
                    f"Row {row_num}: Empty substitution variable '{key}' for {to_address} - "
                    f"this will cause template rendering issues"
                )
        
        return errors, warnings
    
    def _is_valid_email(self, email: str) -> bool:
        """
        Validate email address format
        
        Args:
            email: Email address to validate
            
        Returns:
            True if email format is valid, False otherwise
        """
        if not email or len(email) > 320:  # RFC 5321
            return False
        
        return bool(self.EMAIL_PATTERN.match(email))
    
    @staticmethod
    def get_csv_info(csv_path: str) -> Optional[Dict]:
        """
        Get basic information about a CSV file without full validation
        
        Args:
            csv_path: Path to the CSV file
            
        Returns:
            Dictionary with file info or None if file cannot be read
        """
        try:
            csv_file = Path(csv_path)
            if not csv_file.exists():
                return None
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                row_count = sum(1 for _ in reader)
                
                # Reset to get headers
                f.seek(0)
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []
            
            return {
                'file_name': csv_file.name,
                'file_size_mb': csv_file.stat().st_size / (1024 * 1024),
                'row_count': row_count,
                'headers': headers,
                'has_to_address': 'To_Address' in headers,
                'substitution_columns': [h for h in headers if h.startswith('sub_')]
            }
        
        except Exception:
            return None



# Convenience function for quick validation
def validate_bulk_email_csv(
    csv_path: str, 
    logger=None, 
    ses_client=None, 
    template_name: Optional[str] = None
) -> ValidationResult:
    """
    Convenience function to validate a CSV file for bulk email sending.
    
    This is a simple wrapper around CSVValidator for easy use from any module.
    
    Args:
        csv_path: Path to the CSV file to validate
        logger: Optional logger instance
        ses_client: Optional SES client for template variable validation
        template_name: Optional template name to validate CSV columns against
        
    Returns:
        ValidationResult object with validation details
        
    Example:
        from modules.csv_validator import validate_bulk_email_csv
        
        result = validate_bulk_email_csv(
            "recipients.csv", 
            ses_client=ses_client,
            template_name="MyTemplate"
        )
        if result.is_valid:
            print(f"Valid! {result.valid_row_count} recipients")
        else:
            print(f"Errors: {result.errors}")
    """
    validator = CSVValidator(logger=logger, ses_client=ses_client)
    return validator.validate_csv_file(
        csv_path=csv_path,
        check_duplicates=True,
        max_rows=50000,
        template_name=template_name
    )
