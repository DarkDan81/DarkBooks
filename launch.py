"""Stable portable entry point, independent of Python registry/PYTHONPATH settings."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        from book_illustrator.app_jobs import worker_main
        worker_main(sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=='--self-test':
        import ssl,sqlite3,PIL,lxml.etree,book_illustrator.webapp
        from book_illustrator.app_jobs import RESOURCES
        assert (RESOURCES/'schemas/FictionBook.xsd').is_file()
        assert (RESOURCES/'workflows/qwen21-0-reference-api.json').is_file()
        print('Portable imports and resources OK')
    else:
        from book_illustrator.webapp import main
        main()
