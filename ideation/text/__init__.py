"""PT1: rule-based text extraction from earnings-call transcripts and presentations (05 §7.1).

Reads gzip JSON-lines page files from the Scraper checkout (read-only, ``reader.py`` only) and writes parquet under
``IDEATION_DATA_DIR/text`` (``extract.py`` only). No lake writes, no network, no LLM, no text in git.
"""
