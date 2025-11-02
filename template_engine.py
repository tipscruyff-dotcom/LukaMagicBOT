"""
Simple Template Engine for LukaMagicBOT
Renders HTML templates with variable substitution
"""
import os
from typing import Dict, Any


def render_template(template_name: str, title: str = "", **context: Any) -> str:
    """
    Render HTML template with context variables
    
    Args:
        template_name: Name of template file (without .html)
        title: Page title
        **context: Variables to substitute in template
    
    Returns:
        Rendered HTML string
    """
    try:
        # Load base template
        base_path = os.path.join("templates", "base.html")
        with open(base_path, "r", encoding="utf-8") as f:
            base_template = f.read()
        
        # Load specific template
        template_path = os.path.join("templates", f"{template_name}.html")
        with open(template_path, "r", encoding="utf-8") as f:
            content_template = f.read()
        
        # Substitute variables in content template
        content = substitute_variables(content_template, context)
        
        # Substitute in base template
        final_html = base_template.replace("{{ title }}", title)
        final_html = final_html.replace("{{ content }}", content)
        
        return final_html
        
    except FileNotFoundError as e:
        # Fallback to simple template if file not found
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>{title}</title></head>
        <body>
            <h1>Template Error</h1>
            <p>Template file not found: {template_name}</p>
            <p>Error: {str(e)}</p>
        </body>
        </html>
        """
    except Exception as e:
        # Fallback for any other error
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>{title}</title></head>
        <body>
            <h1>Template Error</h1>
            <p>Error rendering template: {template_name}</p>
            <p>Error: {str(e)}</p>
        </body>
        </html>
        """


def substitute_variables(template: str, context: Dict[str, Any]) -> str:
    """
    Simple variable substitution in template
    Replaces {{ variable_name }} with context values
    """
    result = template
    
    for key, value in context.items():
        placeholder = f"{{{{ {key} }}}}"
        result = result.replace(placeholder, str(value) if value is not None else "")
    
    return result


def render_simple_page(title: str, content: str) -> str:
    """
    Render a simple page with just title and content
    Fallback when templates are not available
    """
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8"/>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <style>
            body {{
                background: #0b1220;
                color: #e2e8f0;
                font-family: Inter, system-ui, -apple-system, sans-serif;
                max-width: 1100px;
                margin: 32px auto;
                padding: 0 16px;
            }}
            .button {{
                background: #6366f1;
                color: #fff;
                padding: 9px 14px;
                border-radius: 8px;
                text-decoration: none;
                margin: 5px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 20px 0;
            }}
            th, td {{
                padding: 12px;
                border-bottom: 1px solid #1f2937;
                text-align: left;
            }}
            th {{
                background: #111827;
                color: #f1f5f9;
            }}
        </style>
    </head>
    <body>
        {content}
    </body>
    </html>
    """