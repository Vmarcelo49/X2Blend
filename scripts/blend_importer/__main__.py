"""blend_importer.__main__ — entry point for ``python -m blend_importer``.

Python's ``-m`` flag runs the ``__main__.py`` of the named package; this
file is a thin shim around ``blend_importer.main:main`` so the documented
``python -m blend_importer <args>`` invocation works out of the box.
"""

from blend_importer.main import main

if __name__ == "__main__":
    main()
