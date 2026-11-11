"""P6 live path: live feature build, scoring with the shipped models, calibration, OOD and neutral rank.

Live-only code. It uses exactly the backtest feature builders, R1 transform and model objects; nothing here reads
labels except the research-challenger fit (``challenger.py``), which goes through ``load_labels(unsealed_only(...))``.
"""
