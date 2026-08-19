from config_store import load_config
cfg = load_config()
print('boxes', len(cfg.boxes), 'samples', len(cfg.samples))
