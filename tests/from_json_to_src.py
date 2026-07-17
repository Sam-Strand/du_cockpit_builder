from cockpit_builder import DUConfigExtractor

# Быстрое извлечение из конфига
def quick_extract(config_path, output_path):
    extractor = DUConfigExtractor(config_path, output_path)
    return extractor.extract()

if __name__ == '__main__':
    # Использование
    quick_extract('tests/config.json', 'tests/my_project')
    print('Done!')