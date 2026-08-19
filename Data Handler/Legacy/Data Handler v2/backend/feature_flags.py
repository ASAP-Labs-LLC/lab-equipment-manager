class FeatureFlags:
    def __init__(self, config):
        self.features = config.get('features', {})

    def is_enabled(self, feature_name):
        return self.features.get(feature_name, False)
