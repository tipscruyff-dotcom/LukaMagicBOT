#!/usr/bin/env python3
# service_cleanup_toolkit.py
# Unified interface for all service cleanup tools

import os
import sys
import argparse
import subprocess
import logging
import textwrap

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-toolkit")

def print_banner():
    """Print a pretty banner for the toolkit"""
    banner = """
    ╔═════════════════════════════════════════════════════════╗
    ║                                                         ║
    ║   Service Cleanup Toolkit v2.0                          ║
    ║   LukaMagicBOT Service Message Management Suite         ║
    ║                                                         ║
    ╚═════════════════════════════════════════════════════════╝
    """
    print(banner)

def print_tool_description(tool_name):
    """Print the description for a specific tool"""
    descriptions = {
        "monitor": """
        🔍 MONITOR TOOL
        -------------
        Monitors the service message cleanup system, checks success rates, 
        and sends alerts when issues are detected.
        
        Common uses:
        - Continuous monitoring of cleanup performance
        - Periodic health checks of the system
        - Alert generation when success rate drops below threshold
        - Data collection for system reliability metrics
        """,
        
        "logger": """
        📝 LOGGER TOOL
        -----------
        Provides real-time logging and tracking of service message cleanup 
        activities with detailed statistics.
        
        Common uses:
        - Real-time observation of cleanup events
        - Troubleshooting cleanup issues as they happen
        - Collecting statistics on message types and chat activity
        - Detailed error tracking and analysis
        """,
        
        "viewer": """
        📊 VIEWER TOOL
        -----------
        Visualizes historical data from the service message cleanup system
        with charts, graphs, and statistical analysis.
        
        Common uses:
        - Long-term trend analysis of cleanup performance
        - Identifying problematic chats or message patterns
        - Generating reports and visualizations for stakeholders
        - Finding correlation between errors and specific conditions
        """,
        
        "simulator": """
        🧪 SIMULATOR TOOL
        -------------
        Tests the service message cleanup system with simulated service
        messages to verify detection and deletion functionality.
        
        Common uses:
        - Testing cleanup functionality with various message types
        - Verifying behavior with edge cases and unusual content
        - Measuring detection and deletion success rates
        - Regression testing after system changes
        """
    }
    
    if tool_name in descriptions:
        print(textwrap.dedent(descriptions[tool_name]))

def check_dependencies():
    """Check if required dependencies are available"""
    try:
        from db import SessionLocal
        from models import ServiceMessageSeen
        return True
    except ImportError:
        return False

def run_tool(tool_name, args):
    """Run the specified tool with the provided arguments"""
    # Check if we're in the right directory
    if not check_dependencies():
        logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
        logger.error("   Try: cd /path/to/project && python tools/service_cleanup_toolkit.py")
        return False
        
    # Build command based on tool name
    if tool_name == "monitor":
        script = "tools/service_cleanup_monitor.py"
    elif tool_name == "logger":
        script = "tools/service_cleanup_logger.py"
    elif tool_name == "viewer":
        script = "tools/service_cleanup_viewer.py"
    elif tool_name == "simulator":
        script = "tools/service_cleanup_simulator.py"
    else:
        logger.error(f"❌ Unknown tool: {tool_name}")
        return False
    
    # Build command
    cmd = [sys.executable, script]
    if args:
        cmd.extend(args)
        
    # Run the command
    try:
        logger.info(f"🚀 Running {tool_name} tool...")
        process = subprocess.run(cmd)
        return process.returncode == 0
    except Exception as e:
        logger.error(f"❌ Error running {tool_name} tool: {e}")
        return False

def verify_tool_exists(tool_name):
    """Check if the specified tool script exists"""
    tool_paths = {
        "monitor": "tools/service_cleanup_monitor.py",
        "logger": "tools/service_cleanup_logger.py",
        "viewer": "tools/service_cleanup_viewer.py",
        "simulator": "tools/service_cleanup_simulator.py"
    }
    
    if tool_name not in tool_paths:
        return False
        
    return os.path.exists(tool_paths[tool_name])

def main():
    parser = argparse.ArgumentParser(
        description="Unified toolkit for service message cleanup management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python tools/service_cleanup_toolkit.py monitor --hours 24
          python tools/service_cleanup_toolkit.py logger --tail
          python tools/service_cleanup_toolkit.py viewer --visualize
          python tools/service_cleanup_toolkit.py simulator --template join_standard
        """)
    )
    
    # Set up subparsers for each tool
    subparsers = parser.add_subparsers(dest='tool', help='Tool to run')
    
    # Monitor parser
    monitor_parser = subparsers.add_parser('monitor', help='Run the monitoring tool')
    monitor_parser.add_argument('args', nargs='*', help='Arguments to pass to the monitor tool')
    
    # Logger parser
    logger_parser = subparsers.add_parser('logger', help='Run the logger tool')
    logger_parser.add_argument('args', nargs='*', help='Arguments to pass to the logger tool')
    
    # Viewer parser
    viewer_parser = subparsers.add_parser('viewer', help='Run the viewer tool')
    viewer_parser.add_argument('args', nargs='*', help='Arguments to pass to the viewer tool')
    
    # Simulator parser
    simulator_parser = subparsers.add_parser('simulator', help='Run the simulator tool')
    simulator_parser.add_argument('args', nargs='*', help='Arguments to pass to the simulator tool')
    
    # Parse args
    args = parser.parse_args()
    
    # Print banner
    print_banner()
    
    if not args.tool:
        parser.print_help()
        return 1
    
    # Print tool description
    print_tool_description(args.tool)
    
    # Verify tool exists
    if not verify_tool_exists(args.tool):
        logger.error(f"❌ Tool script for '{args.tool}' not found. Make sure all tool scripts are in the tools directory.")
        return 1
    
    # Run the selected tool
    success = run_tool(args.tool, args.args)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())