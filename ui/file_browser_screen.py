#!/usr/bin/env python3
"""
File Browser Screen
Modal screen with DirectoryTree for browsing and selecting files
"""

from pathlib import Path
from textual.screen import ModalScreen
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import DirectoryTree, Button, Label, Static
from textual import on


class FileBrowserScreen(ModalScreen[str]):
    """Modal screen for browsing and selecting files."""
    
    DEFAULT_CSS = """
    FileBrowserScreen {
        align: center middle;
    }
    
    #file-browser-dialog {
        width: 80;
        height: 35;
        background: $surface;
        border: thick $primary;
        padding: 1;
    }
    
    #browser-title {
        height: 3;
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
        content-align: center middle;
    }
    
    #browser-path {
        height: 2;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    
    #directory-tree-container {
        height: 1fr;
        border: solid $secondary;
        margin-bottom: 1;
    }
    
    #browser-buttons {
        dock: bottom;
        height: 3;
        align: center middle;
    }
    
    #browser-nav-buttons {
        height: 3;
        align: center middle;
        margin-bottom: 1;
    }
    
    #browser-nav-buttons Button {
        min-width: 12;
        margin: 0 1;
    }
    
    #selected-file-label {
        dock: bottom;
        height: 2;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    """
    
    def __init__(self, initial_path: str = None, file_filter: str = "*.csv"):
        super().__init__()
        self.initial_path = Path(initial_path) if initial_path else Path.cwd()
        self.file_filter = file_filter
        self.selected_path = None
        self.current_path = self.initial_path
    
    def compose(self):
        """Create the file browser dialog."""
        with Container(id="file-browser-dialog"):
            yield Static("CSV File Browser", id="browser-title")
            yield Static(f"Current: {self.initial_path}", id="browser-path")
            
            with Horizontal(id="browser-nav-buttons"):
                yield Button("< Back", variant="primary", id="up-dir-btn")
            
            with Container(id="directory-tree-container"):
                yield DirectoryTree(str(self.initial_path), id="directory-tree")
            
            yield Label("Select a file and click 'Select' or double-click a file", id="selected-file-label")
            
            with Horizontal(id="browser-buttons"):
                yield Button("Select", variant="primary", id="select-file-btn")
                yield Button("Cancel", variant="default", id="cancel-browser-btn")
    
    def on_mount(self) -> None:
        """Focus the directory tree when mounted."""
        tree = self.query_one("#directory-tree", DirectoryTree)
        tree.focus()
    
    @on(DirectoryTree.FileSelected)
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Handle file selection from directory tree."""
        file_path = event.path
        
        # Check if it matches our filter
        if self.file_filter == "*" or file_path.suffix == self.file_filter.replace("*", ""):
            self.selected_path = str(file_path)
            # Update the selected file label
            selected_label = self.query_one("#selected-file-label", Label)
            selected_label.update(f"Selected: {file_path.name}")
            
            # Auto-select on double-click by dismissing with the path
            self.dismiss(str(file_path))
        else:
            self.app.notify(
                f"Please select a {self.file_filter} file",
                severity="warning"
            )
    
    @on(DirectoryTree.DirectorySelected)
    def on_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """Handle directory selection - update the current path display."""
        dir_path = event.path
        self.current_path = dir_path
        path_display = self.query_one("#browser-path", Static)
        path_display.update(f"Current: {dir_path}")
    
    @on(Button.Pressed, "#up-dir-btn")
    def on_up_button(self) -> None:
        """Navigate to parent directory."""
        parent_path = self.current_path.parent
        if parent_path != self.current_path:  # Not at root
            tree = self.query_one("#directory-tree", DirectoryTree)
            tree.path = str(parent_path)
            tree.reload()
            self.current_path = parent_path
            path_display = self.query_one("#browser-path", Static)
            path_display.update(f"Current: {parent_path}")
        else:
            self.app.notify("Already at root directory", severity="information")
    
    @on(Button.Pressed, "#select-file-btn")
    def on_select_button(self) -> None:
        """Handle select button press."""
        if self.selected_path:
            self.dismiss(self.selected_path)
        else:
            # Try to get the currently highlighted node
            tree = self.query_one("#directory-tree", DirectoryTree)
            if tree.cursor_node and tree.cursor_node.data:
                path = tree.cursor_node.data.path
                if path.is_file():
                    # Check if it matches our filter
                    if self.file_filter == "*" or path.suffix == self.file_filter.replace("*", ""):
                        self.dismiss(str(path))
                    else:
                        self.app.notify(
                            f"Please select a {self.file_filter} file",
                            severity="warning"
                        )
                else:
                    self.app.notify("Please select a file, not a directory", severity="warning")
            else:
                self.app.notify("Please select a file first", severity="warning")
    
    @on(Button.Pressed, "#cancel-browser-btn")
    def on_cancel_button(self) -> None:
        """Handle cancel button press."""
        self.dismiss(None)
