import json, traceback
from models import AppConfig

with open('lab_manager_config.json', 'r', encoding='utf-8') as f:
    raw = json.load(f)
print('json ok; keys:', list(raw)[:8])
try:
    cfg = AppConfig.from_dict(raw)
    print('AppConfig ok; boxes:', len(cfg.boxes), 'samples:', len(cfg.samples))
except Exception as e:
    print('AppConfig.from_dict failed:', type(e).__name__, e)
    traceback.print_exc()
