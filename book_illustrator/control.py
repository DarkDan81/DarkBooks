"""Cooperative pause: finish the current durable operation before stopping."""
import os
from pathlib import Path

class PauseRequested(RuntimeError):
    pass

def check_pause():
    marker = os.environ.get('BOOK_ILLUSTRATOR_STOP_FILE')
    if marker and Path(marker).exists():
        raise PauseRequested('Pause requested; saved results will be reused on resume')
