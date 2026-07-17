# cockpit_builder/from_json_to_src.py

import json
import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class DUConfigExtractor:
    """Извлекает файловую структуру из конфигурации Dual Universe"""

    def __init__(self, config_path: str, output_path: str):
        self.config_path = Path(config_path)
        self.output_path = Path(output_path)
        self.config = None

    def extract(self) -> bool:
        """Извлекает структуру из конфига"""
        try:
            # Загружаем конфиг
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)

            # Создаем базовые папки
            self.output_path.mkdir(parents=True, exist_ok=True)

            # Создаем src папку
            src_dir = self.output_path / 'src'
            src_dir.mkdir(exist_ok=True)

            # Создаем slots.json
            self._create_slots_json()

            # Создаем обработчики
            handlers_created = self._create_handlers(src_dir)

            logger.info(f'✅ Project structure created in: {self.output_path}')
            logger.info(f'   Slots: {len(self.config.get("slots", {}))}')
            logger.info(f'   Handlers: {len(self.config.get("handlers", []))}')
            logger.info(f'   Files created: {handlers_created}')
            return True

        except json.JSONDecodeError as e:
            logger.error(f'❌ Error parsing config JSON: {e}')
            return False
        except KeyError as e:
            logger.error(f'❌ Missing required field in config: {e}')
            return False
        except Exception as e:
            logger.error(f'❌ Error extracting structure: {e}')
            return False

    def _create_slots_json(self):
        """Создает slots.json с сохранением всех параметров"""
        slots_data = self.config.get('slots', {})

        # Восстанавливаем формат slots.json с полными данными
        slots_config = {}

        for key, slot_info in slots_data.items():
            name = slot_info.get('name')
            if not name:
                continue

            # Получаем type
            slot_type = slot_info.get('type', {})

            # Собираем конфиг слота
            slot_config = {}

            # Добавляем class если есть
            if 'class' in slot_type:
                slot_config['class'] = slot_type['class']

            # Добавляем select если есть
            if 'select' in slot_type:
                slot_config['select'] = slot_type['select']

            # Добавляем group если есть
            if 'group' in slot_type:
                slot_config['group'] = slot_type['group']

            # Добавляем category если есть
            if 'category' in slot_type:
                slot_config['category'] = slot_type['category']

            # Если есть только class - сохраняем как строку для компактности
            if len(slot_config) == 1 and 'class' in slot_config:
                slots_config[name] = slot_config['class']
            elif slot_config:
                slots_config[name] = slot_config
            else:
                # Если ничего нет - сохраняем как пустой объект
                slots_config[name] = {}

        # Сохраняем в формате, который ожидает DUConfigBuilder
        slots_file = self.output_path / 'slots.json'
        with open(slots_file, 'w', encoding='utf-8') as f:
            json.dump(slots_config, f, indent=2, ensure_ascii=False)

        logger.info(f'   Created slots.json with {len(slots_config)} slots')

        # Логируем информацию о слотах с select
        slots_with_select = {name: cfg for name, cfg in slots_config.items()
                             if isinstance(cfg, dict) and 'select' in cfg}
        if slots_with_select:
            logger.info(
                f'   Slots with manual selection: {len(slots_with_select)}')
            for name, cfg in slots_with_select.items():
                logger.debug(f'     {name}: select={cfg["select"]}')

    def _create_handlers(self, src_dir: Path) -> int:
        """Создает файлы обработчиков. Возвращает количество созданных файлов"""
        handlers = self.config.get('handlers', [])

        if not handlers:
            logger.warning('   No handlers found in config')
            return 0

        # Группируем обработчики по слотам
        handlers_by_slot = {}
        for handler in handlers:
            slot_key = handler.get('filter', {}).get('slotKey', '')
            if slot_key:
                if slot_key not in handlers_by_slot:
                    handlers_by_slot[slot_key] = []
                handlers_by_slot[slot_key].append(handler)

        # Создаем файлы только для слотов с обработчиками
        slots = self.config.get('slots', {})
        total_files = 0

        # Получаем список слотов, у которых есть обработчики
        slots_with_handlers = set(handlers_by_slot.keys())

        for slot_key in slots_with_handlers:
            slot_info = slots.get(slot_key)
            if not slot_info:
                logger.warning(
                    f'   Slot key {slot_key} not found in slots configuration')
                continue

            slot_name = slot_info.get('name', f'slot_{slot_key}')

            # Создаем папку слота
            slot_dir = src_dir / slot_name
            slot_dir.mkdir(exist_ok=True)

            # Получаем обработчики для этого слота
            slot_handlers = handlers_by_slot.get(slot_key, [])

            if slot_handlers:
                files_created = self._create_handler_files(
                    slot_dir, slot_handlers)
                total_files += files_created
                logger.info(
                    f'   Slot "{slot_name}" ({slot_key}): {files_created} handlers')

        return total_files

    def _create_handler_files(self, slot_dir: Path, handlers: list) -> int:
        """Создает файлы обработчиков в папке слота. Возвращает количество созданных файлов"""
        created_count = 0

        for handler in handlers:
            filter_info = handler.get('filter', {})
            signature = filter_info.get('signature', '')
            args = filter_info.get('args', [])
            code = handler.get('code', '')

            if not signature:
                logger.warning(f'   Handler without signature, skipping')
                continue

            # Генерируем имя файла из сигнатуры
            filename = self._generate_filename(signature, args)

            if not filename:
                logger.warning(
                    f'   Could not generate filename for: {signature}')
                continue

            file_path = slot_dir / f'{filename}.lua'

            # Записываем код
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)

            created_count += 1
            logger.debug(f'   Created: {file_path.name}')

        return created_count

    def _generate_filename(self, signature: str, args: list) -> Optional[str]:
        """
        Генерирует имя файла из сигнатуры и аргументов.
        Поддерживает различные форматы:
        - onStart() -> onStart
        - onActionStart(brake) -> onActionStart(brake)
        - onActionStop(gear) -> onActionStop(gear)
        """
        if not signature:
            return None

        # Проверяем, есть ли уже аргументы в сигнатуре
        match = re.match(r'^(\w+)\(([^)]*)\)$', signature)

        if match:
            # Сигнатура уже содержит аргументы
            func_name = match.group(1)
            sig_args = match.group(2).strip()

            # Если в сигнатуре уже есть аргументы, используем их
            if sig_args:
                # Проверяем, есть ли аргументы в filter
                if args and len(args) > 0:
                    # Берем имя аргумента из filter
                    arg_name = args[0].get('value', '') if isinstance(
                        args[0], dict) else str(args[0])
                    # Если имена совпадают, используем сигнатуру как есть
                    if arg_name == sig_args:
                        return signature
                    # Иначе используем аргумент из filter
                    return f"{func_name}({arg_name})"
                return signature
            else:
                # Сигнатура без аргументов, но args есть
                if args and len(args) > 0:
                    arg_name = args[0].get('value', '') if isinstance(
                        args[0], dict) else str(args[0])
                    if arg_name:
                        return f"{func_name}({arg_name})"
                return func_name

        # Если сигнатура без скобок (например, onStart)
        if args and len(args) > 0:
            arg_name = args[0].get('value', '') if isinstance(
                args[0], dict) else str(args[0])
            if arg_name:
                return f"{signature}({arg_name})"

        return signature


def main():
    """Пример использования"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Extract DU project structure from config')
    parser.add_argument('config', help='Path to config.json file')
    parser.add_argument('-o', '--output', default='./extracted_project',
                        help='Output directory (default: ./extracted_project)')
    parser.add_argument('--verbose', action='store_true',
                        help='Show detailed output')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Извлечение
    extractor = DUConfigExtractor(args.config, args.output)
    success = extractor.extract()

    if success:
        # Показываем структуру
        print(f'\n📁 Project structure:')
        for root, dirs, files in os.walk(args.output):
            level = root.replace(args.output, '').count(os.sep)
            indent = '  ' * level
            dir_name = os.path.basename(root)
            if level == 0:
                print(f'📂 {dir_name}/')
            else:
                print(f'{indent}📂 {dir_name}/')

            subindent = '  ' * (level + 1)
            for file in files:
                if file.endswith('.lua'):
                    print(f'{subindent}📄 {file}')
                else:
                    print(f'{subindent}📋 {file}')
    else:
        print('\n❌ Extraction failed. Check the logs for details.')


if __name__ == '__main__':
    main()
