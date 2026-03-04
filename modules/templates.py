#!/usr/bin/env python3
"""
Templates Module
Handles template management functionality
"""

from typing import Dict, List, Optional, Any
from datetime import datetime

from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Button
from textual import on

from aws.ses_client import SESClient
from ui.screens import TemplateFormScreen, TemplatePreviewScreen
from config.settings import settings


class TemplatesManager:
    """Manages template operations and UI interactions."""
    
    def __init__(self, app, ses_client: Optional[SESClient] = None):
        self.app = app
        self.ses_client = ses_client
        self.templates = []
    
    def set_ses_client(self, ses_client: SESClient):
        """Set the SES client for template operations."""
        self.ses_client = ses_client
    
    async def refresh_templates(self, force_refresh: bool = False) -> None:
        """Refresh templates data from Amazon SES with caching support."""
        if not self.ses_client:
            return
        
        try:
            self.templates = self.ses_client.get_templates(force_refresh=force_refresh)
            await self.update_templates_table()
        except Exception as e:
            self.app.notify(f"Error refreshing templates: {str(e)}", severity="error")
    
    async def update_templates_table(self) -> None:
        """Update the templates data table."""
        try:
            table = self.app.query_one("#templates-table", DataTable)
        except:
            # Table doesn't exist yet, skip update
            return
            
        table.clear(columns=True)
        
        if not self.templates:
            table.add_columns("Status")
            table.add_row("No templates found. Click 'Create Template' to add one.")
            return
        
        # Add columns
        table.add_columns("Name", "Subject", "Created")
        
        # Add rows
        for template in self.templates:
            # Amazon SES API returns template content with different field names
            name = template.get('TemplateName', template.get('Name', 'N/A'))
            subject = template.get('Subject', template.get('SubjectPart', 'N/A'))
            if subject and len(subject) > 50:
                subject = subject[:50] + '...'
            elif not subject:
                subject = 'N/A'
            
            # CreatedTimestamp might be in different formats or missing
            created = template.get('CreatedTimestamp', template.get('Timestamp', 'N/A'))
            if isinstance(created, datetime):
                created = created.strftime('%Y-%m-%d %H:%M')
            elif created == 'N/A':
                created = 'Unknown'
            
            table.add_row(name, subject, str(created))
    
    async def create_template_worker(self) -> None:
        """Worker for creating a new template."""
        if not self.ses_client:
            self.app.notify("No SES connection available", severity="error")
            return
            
        template_data = await self.app.push_screen_wait(TemplateFormScreen())
        if template_data:
            try:
                self.ses_client.create_template(template_data)
                self.app.notify(f"Template '{template_data['TemplateName']}' created successfully", severity="success")
                await self.refresh_templates()
            except Exception as e:
                self.app.notify(f"Error creating template: {str(e)}", severity="error")
    
    async def edit_template_worker(self, template: Dict[str, Any]) -> None:
        """Worker for editing a template."""
        if not self.ses_client:
            self.app.notify("No SES connection available", severity="error")
            return
            
        template_data = await self.app.push_screen_wait(TemplateFormScreen(template))
        
        if template_data:
            try:
                self.ses_client.update_template(template_data)
                self.app.notify(f"Template '{template_data['TemplateName']}' updated successfully", severity="success")
                await self.refresh_templates()
            except Exception as e:
                self.app.notify(f"Error updating template: {str(e)}", severity="error")
    
    async def preview_template_worker(self, template: Dict[str, Any]) -> None:
        """Worker for previewing a template."""
        try:
            await self.app.push_screen_wait(TemplatePreviewScreen(template))
        except Exception as e:
            self.app.notify(f"Error previewing template: {str(e)}", severity="error")
    
    async def delete_template(self, template_name: str) -> None:
        """Delete a template."""
        if not self.ses_client:
            self.app.notify("No SES connection available", severity="error")
            return
            
        try:
            self.ses_client.delete_template(template_name)
            self.app.notify(f"Template '{template_name}' deleted successfully", severity="success")
            await self.refresh_templates()
        except Exception as e:
            self.app.notify(f"Error deleting template: {str(e)}", severity="error")
    
    def get_selected_template(self) -> Optional[Dict[str, Any]]:
        """Get the currently selected template from the table."""
        try:
            table = self.app.query_one("#templates-table", DataTable)
            if table.cursor_row is not None and table.cursor_row < len(self.templates):
                return self.templates[table.cursor_row]
        except:
            pass  # nosec B110 - Intentional - widget may not be mounted
        return None
    
    def get_template_names(self) -> List[str]:
        """Get list of template names for dropdowns."""
        return [t.get('TemplateName', '') for t in self.templates]


def create_templates_tab_content():
    """Create the content for the templates tab."""
    from textual.containers import Container, Horizontal
    from textual.widgets import DataTable, Button, Static
    
    return [
        Static("📝 Email Templates", classes="section-title"),
        DataTable(id="templates-table", cursor_type="row"),
        Horizontal(
            Button("Create Template", variant="primary", id="create-template"),
            Button("Edit Template", variant="default", id="edit-template"),
            Button("Preview Template", variant="success", id="preview-template"),
            Button("Delete Template", variant="error", id="delete-template"),
            Button("Refresh", variant="default", id="refresh-templates"),
            id="template-actions"
        )
    ]
