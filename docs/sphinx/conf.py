import os
import sys
# Add project root to path for autodoc discovery
sys.path.insert(0, os.path.abspath('../../'))

project = 'FinParse API'
copyright = '2026, Google DeepMind & Darshan-dlr Pair Programming'
author = 'Antigravity AI & Darshan-dlr'
release = '1.0.0'

# Sphinx extension modules
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',        # support google & numpy style docstrings
    'sphinx.ext.githubpages',
]

templates_path = ['_templates']
exclude_patterns = ['build', 'Thumbs.db', '.DS_Store']

# Modern Furo Theme Configuration
html_theme = 'furo'
html_static_path = ['_static']
html_css_files = [
    'custom.css',
]

html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#4f46e5",    # Indigo
        "color-brand-content": "#0f766e",    # Teal
        "font-stack": "Outfit, sans-serif",
    },
    "dark_css_variables": {
        "color-brand-primary": "#818cf8",    # Violet
        "color-brand-content": "#2dd4bf",    # Emerald/Teal
        "font-stack": "Outfit, sans-serif",
    },
}
