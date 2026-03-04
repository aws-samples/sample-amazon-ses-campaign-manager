#!/usr/bin/env python3
"""
UI Screens Module
Contains modal screens and dialogs for the SES Manager
"""

import json
from typing import Dict, List, Optional, Any

from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, TextArea, Select, Label, TabbedContent
from textual.screen import ModalScreen
from textual import on

from config.settings import settings


def debug_print(message: str) -> None:
    """Print debug message only if debug logging is enabled."""
    if settings.get('app.debug_logging', False):
        print(f"DEBUG: {message}")


class ProfileSelectionScreen(ModalScreen[Dict[str, str]]):
    """Screen for selecting AWS profile and region."""
    
    def __init__(self, profiles: List[str]) -> None:
        super().__init__()
        self.profiles = profiles
        # AWS regions that support SES
        self.regions = [
            ("us-east-1", "US East (N. Virginia)"),
            ("us-west-2", "US West (Oregon)"),
            ("eu-west-1", "Europe (Ireland)"),
            ("eu-central-1", "Europe (Frankfurt)"),
            ("ap-southeast-1", "Asia Pacific (Singapore)"),
            ("ap-southeast-2", "Asia Pacific (Sydney)"),
            ("ap-northeast-1", "Asia Pacific (Tokyo)"),
            ("ca-central-1", "Canada (Central)"),
            ("eu-west-2", "Europe (London)"),
            ("us-gov-west-1", "AWS GovCloud (US-West)"),
            ("ap-south-1", "Asia Pacific (Mumbai)"),
        ]
    
    def compose(self):
        # Get saved AWS config
        aws_config = settings.get_aws_config()
        default_profile = aws_config.get('profile', 'default')
        default_region = aws_config.get('region', 'us-east-1')
        
        with Container(id="profile-dialog"):
            yield Label("AWS Configuration", id="profile-title")
            
            yield Label("AWS Profile:")
            yield Select(
                [(profile, profile) for profile in self.profiles],
                id="profile-select",
                value=default_profile if default_profile in self.profiles else (self.profiles[0] if self.profiles else None)
            )
            
            yield Label("AWS Region:")
            # Find the default region in our regions list
            default_region_option = None
            for code, display_name in self.regions:
                if code == default_region:
                    default_region_option = code
                    break
            
            # If no default found, use the first region
            if default_region_option is None and self.regions:
                default_region_option = self.regions[0][0]
            
            yield Select(
                self.regions,
                id="region-select",
                allow_blank=False
            )
            
            with Horizontal(id="profile-buttons"):
                yield Button("Connect", variant="primary", id="select-profile")
                yield Button("Cancel", variant="default", id="cancel-profile")
    
    def on_mount(self) -> None:
        """Set default values after mounting."""
        # Get saved AWS config
        aws_config = settings.get_aws_config()
        default_region = aws_config.get('region', 'us-east-1')
        
        # Find the default region in our regions list
        default_region_option = None
        for code, display_name in self.regions:
            if code == default_region:
                default_region_option = code
                break
        
        # If no default found, use the first region
        if default_region_option is None and self.regions:
            default_region_option = self.regions[0][0]
        
        # Set the region select value
        if default_region_option:
            try:
                region_widget = self.query_one("#region-select", Select)
                region_widget.value = default_region_option
            except Exception:
                # If setting the value fails, just continue without it
                pass  # nosec B110 - Intentional - widget may not be mounted
    
    @on(Button.Pressed, "#select-profile")
    def select_profile(self) -> None:
        profile_widget = self.query_one("#profile-select", Select)
        region_widget = self.query_one("#region-select", Select)
        
        if not profile_widget.value:
            self.notify("Please select an AWS profile", severity="error")
            return
            
        if not region_widget.value:
            self.notify("Please select an AWS region", severity="error")
            return
        
        # Extract region code from the selection
        region_selection = region_widget.value
        region_code = None
        
        for code, display_name in self.regions:
            if display_name == region_selection or code == region_selection:
                region_code = code
                break
        
        if not region_code:
            self.notify("Invalid region selection", severity="error")
            return
        
        # Save the selected configuration
        settings.set_aws_config(profile_widget.value, region_code)
        
        self.dismiss({
            "profile": profile_widget.value,
            "region": region_code
        })
    
    @on(Button.Pressed, "#cancel-profile")
    def cancel_selection(self) -> None:
        self.dismiss(None)


class TemplatePreviewScreen(ModalScreen[None]):
    """Screen for previewing email templates with HTML rendering."""
    
    def __init__(self, template_data: Dict[str, Any]) -> None:
        super().__init__()
        self.template_data = template_data
    
    def compose(self):
        from textual.containers import Container, Horizontal, ScrollableContainer
        from textual.widgets import Button, Label, Static, TextArea, TabbedContent, TabPane
        
        template_name = self.template_data.get('TemplateName', 'Unknown Template')
        subject = self.template_data.get('Subject', self.template_data.get('SubjectPart', ''))
        html_content = self.template_data.get('Html', self.template_data.get('HtmlPart', ''))
        text_content = self.template_data.get('Text', self.template_data.get('TextPart', ''))
        
        with Container(id="template-preview"):
            yield Label(f"Template Preview: {template_name}", id="preview-title")
            
            # Template info section
            with Container(id="template-info"):
                yield Label(f"Subject: {subject}", id="template-subject")
            
            # Tabbed content for different views
            with TabbedContent(initial="html-source"):
                with TabPane("HTML Source", id="html-source"):
                    with ScrollableContainer():
                        if html_content:
                            yield TextArea(html_content, read_only=True, id="html-source-area")
                        else:
                            yield Static("No HTML content available")
                
                with TabPane("Text Version", id="text-version"):
                    with ScrollableContainer():
                        if text_content:
                            yield TextArea(text_content, read_only=True, id="text-content-area")
                        else:
                            yield Static("No text content available")
            
            with Horizontal(id="preview-buttons"):
                if html_content:
                    yield Button("View in Browser", variant="primary", id="view-browser")
                yield Button("Close", variant="default", id="close-preview")
    
    def on_mount(self) -> None:
        """Handle initial mount - show/hide browser button based on active tab."""
        self._update_browser_button_visibility()
    
    def on_tabbed_content_tab_activated(self, event) -> None:
        """Handle tab changes to show/hide the browser preview button."""
        self._update_browser_button_visibility()
    
    def _update_browser_button_visibility(self) -> None:
        """Show browser button only on HTML Source tab, hide on Text Version tab."""
        try:
            tabbed_content = self.query_one(TabbedContent)
            browser_button = self.query_one("#view-browser", Button)
            
            # Show button only when HTML Source tab is active
            if tabbed_content.active == "html-source":
                browser_button.display = True
            else:
                browser_button.display = False
        except Exception:
            pass  # Button or tab might not exist
    
    def _create_browser_preview(self, html_content: str, template_name: str) -> str:
        """Create a temporary HTML file and open it in browser."""
        import tempfile
        import webbrowser
        
        try:
            # Create a temporary HTML file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                # Create a complete HTML document
                full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Template Preview: {template_name}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .preview-container {{
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header {{
            background-color: #007bff;
            color: white;
            padding: 10px 20px;
            margin: -20px -20px 20px -20px;
            border-radius: 8px 8px 0 0;
        }}
    </style>
</head>
<body>
    <div class="preview-container">
        <div class="header">
            <h1>Template Preview: {template_name}</h1>
            <p>This is a preview of your email template as it would appear in a browser.</p>
        </div>
        {html_content}
    </div>
</body>
</html>"""
                f.write(full_html)
                f.flush()  # Ensure file is written before accessing f.name
                temp_file_path = f.name
            
            # Open in browser
            webbrowser.open(f'file://{temp_file_path}')
            return temp_file_path
            
        except Exception as e:
            return f"Error creating browser preview: {str(e)}"
    
    @on(Button.Pressed, "#view-browser")
    def view_in_browser(self) -> None:
        """Open the template in browser for proper HTML rendering."""
        template_name = self.template_data.get('TemplateName', 'Unknown Template')
        html_content = self.template_data.get('Html', self.template_data.get('HtmlPart', ''))
        
        if html_content:
            temp_file = self._create_browser_preview(html_content, template_name)
            if temp_file.startswith("Error"):
                self.notify(temp_file, severity="error")
            else:
                self.notify(f"Template opened in browser. Temporary file: {temp_file}", severity="success")
        else:
            self.notify("No HTML content to preview", severity="warning")
    
    @on(Button.Pressed, "#close-preview")
    def close_preview(self) -> None:
        self.dismiss(None)


class TemplateFormScreen(ModalScreen[Dict[str, Any]]):
    """Screen for creating/editing email templates."""
    
    def __init__(self, template_data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.template_data = template_data or {}
        self.is_edit = bool(template_data)
    
    def compose(self):
        from textual.widgets import Static
        
        with Container(id="template-form"):
            yield Label("Edit Template" if self.is_edit else "Create Template", id="form-title")
            
            # Warning about reserved placeholders
            yield Static(
                "⚠️ WARNING: Do NOT use {{unsubscribe_link}} in your template. "
                "This placeholder is reserved and automatically added by the system when needed.",
                classes="form-warning-text",
                id="unsubscribe-warning"
            )
            
            yield Label("Template Name:")
            yield Input(
                value=self.template_data.get("TemplateName", ""),
                placeholder="Enter template name",
                id="template-name",
                disabled=self.is_edit  # Can't change name when editing
            )
            
            yield Label("Subject:")
            yield Input(
                value=self.template_data.get("Subject", self.template_data.get("SubjectPart", "")),
                placeholder="Enter email subject",
                id="template-subject"
            )
            
            yield Label("HTML Content:")
            yield TextArea(
                text=self.template_data.get("Html", self.template_data.get("HtmlPart", "")),
                id="template-html"
            )
            
            yield Label("Text Content:")
            yield TextArea(
                text=self.template_data.get("Text", self.template_data.get("TextPart", "")),
                id="template-text"
            )
            
            with Horizontal(id="form-buttons"):
                yield Button("Save", variant="primary", id="save-template")
                yield Button("Cancel", variant="default", id="cancel-template")
    
    @on(Button.Pressed, "#save-template")
    def save_template(self) -> None:
        name = self.query_one("#template-name", Input).value.strip()
        subject = self.query_one("#template-subject", Input).value.strip()
        html = self.query_one("#template-html", TextArea).text.strip()
        text = self.query_one("#template-text", TextArea).text.strip()
        
        if not name:
            self.notify("Template name is required", severity="error")
            return
        
        if not subject and not html and not text:
            self.notify("At least one of subject, HTML, or text content is required", severity="error")
            return
        
        # Check for reserved placeholder {{unsubscribe_link}}
        reserved_placeholder = "{{unsubscribe_link}}"
        error_locations = []
        
        if reserved_placeholder in subject:
            error_locations.append("Subject")
        if reserved_placeholder in html:
            error_locations.append("HTML content")
        if reserved_placeholder in text:
            error_locations.append("Text content")
        
        if error_locations:
            locations_str = ", ".join(error_locations)
            self.notify(
                f"ERROR: Template contains reserved placeholder {{{{unsubscribe_link}}}} in: {locations_str}. "
                "This placeholder is automatically added by the system and should not be included in templates.",
                severity="error",
                timeout=10
            )
            return
        
        template_data = {
            "TemplateName": name,
            "SubjectPart": subject,
            "HtmlPart": html,
            "TextPart": text
        }
        
        self.dismiss(template_data)
    
    @on(Button.Pressed, "#cancel-template")
    def cancel_template(self) -> None:
        self.dismiss(None)



class CSVValidationReportScreen(ModalScreen[str]):
    """Screen for displaying CSV validation report with errors and warnings."""
    
    DEFAULT_CSS = """
    CSVValidationReportScreen {
        align: center middle;
    }
    
    #validation-report-dialog {
        width: 90%;
        height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #validation-report-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    
    #validation-summary {
        background: $panel;
        padding: 1;
        margin-bottom: 1;
        border: solid $accent;
    }
    
    #validation-details {
        height: 1fr;
        border: solid $accent;
        background: $panel;
        padding: 1;
    }
    
    #validation-actions {
        margin-top: 1;
        align: center middle;
    }
    """
    
    def __init__(self, validation_result, csv_filename: str) -> None:
        super().__init__()
        self.validation_result = validation_result
        self.csv_filename = csv_filename
    
    def compose(self):
        """Create the validation report dialog."""
        from textual.widgets import Static, TextArea
        
        # Determine status icon and color
        if self.validation_result.is_valid:
            status_icon = "✅"
            status_text = "VALID"
        else:
            status_icon = "❌"
            status_text = "INVALID"
        
        # Build summary
        summary_lines = [
            f"{status_icon} CSV Validation Report: {status_text}",
            f"File: {self.csv_filename}",
            f"Total Rows: {self.validation_result.row_count}",
            f"Valid Rows: {self.validation_result.valid_row_count}",
            f"Errors: {len(self.validation_result.errors)}",
            f"Warnings: {len(self.validation_result.warnings)}"
        ]
        
        # Build details text
        details_lines = []
        
        # Add errors
        if self.validation_result.errors:
            details_lines.append("=" * 60)
            details_lines.append(f"❌ ERRORS ({len(self.validation_result.errors)})")
            details_lines.append("=" * 60)
            for i, error in enumerate(self.validation_result.errors, 1):
                details_lines.append(f"{i}. {error}")
            details_lines.append("")
        
        # Add warnings
        if self.validation_result.warnings:
            details_lines.append("=" * 60)
            details_lines.append(f"⚠️  WARNINGS ({len(self.validation_result.warnings)})")
            details_lines.append("=" * 60)
            for i, warning in enumerate(self.validation_result.warnings, 1):
                details_lines.append(f"{i}. {warning}")
            details_lines.append("")
        
        if not self.validation_result.errors and not self.validation_result.warnings:
            details_lines.append("✅ No issues found! CSV file is ready for use.")
        
        with Container(id="validation-report-dialog"):
            yield Label("📋 CSV Validation Report", id="validation-report-title")
            yield Static("\n".join(summary_lines), id="validation-summary")
            
            # Use TextArea instead of Log to avoid threading issues
            yield TextArea(
                "\n".join(details_lines),
                id="validation-details",
                read_only=True,
                show_line_numbers=False
            )
            
            with Horizontal(id="validation-actions"):
                yield Button("Save Report", variant="primary", id="save-report")
                yield Button("Close", variant="default", id="close-report")
    
    @on(Button.Pressed, "#save-report")
    def handle_save_report(self) -> None:
        """Save the validation report to a file."""
        self.dismiss("save")
    
    @on(Button.Pressed, "#close-report")
    def handle_close_report(self) -> None:
        """Close the validation report."""
        self.dismiss(None)
