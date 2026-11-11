"""MS1: the 1Y bake-off runner (05 §3, §8): matrices, walk-forward, CPCV, tables R-A..R-D, gates, persistence.

Labels are read only through :func:`ideation.labels.loader.load_labels`; nothing here imports ``ideation.lake`` or
``psycopg`` outside ``persist.py`` (which uses the guarded writer).
"""
