"""
Scanner Registry — Standalone Module
=====================================
Extracted ScannerStateRegistry for use across all modules.
Re-exports from dashboard.py for backward compatibility.
"""

from dashboard import ScannerStateRegistry, scanner_watchdog

__all__ = ['ScannerStateRegistry', 'scanner_watchdog']
