#!/bin/bash

# Amazon SES Email Template Manager - Modular Version
# Run script for the modular application

echo "Starting Amazon SES Email Template Manager (Modular Version)..."
echo "Make sure you have:"
echo "1. AWS CLI configured with your credentials"
echo "2. Python dependencies installed (pip install -r requirements.txt)"
echo "3. Proper Amazon SES permissions"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH"
    exit 1
fi

# Check if required modules exist
if [ ! -f "ses_manager_modular.py" ]; then
    echo "Error: ses_manager_modular.py not found"
    exit 1
fi

if [ ! -d "config" ] || [ ! -d "aws" ] || [ ! -d "ui" ] || [ ! -d "modules" ]; then
    echo "Error: Required module directories not found"
    echo "Make sure config/, aws/, ui/, and modules/ directories exist"
    exit 1
fi

# Run the modular application
python3 ses_manager_modular.py
