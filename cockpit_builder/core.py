import json
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString
import re
import time
import logging
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class DUAutoconfBuilder:
    """Сборщик autoconf конфигурации из файловой структуры"""

    # Обязательные системные слоты, которые всегда должны быть
    DEFAULT_SLOTS = {
        'core': {'class': 'CoreUnit'},
        'system': {'class': 'SystemUnit'},
        'unit': {'class': 'ControlUnit'},
        'library': {'class': 'LibraryUnit'}
    }

    def __init__(self, project_path: str, deploy_path: str = None, logger=None):
        self.project_path = Path(project_path)
        self.deploy_path = Path(deploy_path) if deploy_path else None
        self.logger = logger or logging.getLogger(__name__)

        # Начинаем с дефолтных слотов
        self.config = {
            'name': self.project_path.name,
            'slots': self.DEFAULT_SLOTS.copy(),
            'handlers': {}
        }

        self.slot_map = {name: config.copy()
                         for name, config in self.DEFAULT_SLOTS.items()}
        self.handler_groups = {}

    def build(self, deploy_to_game: bool = False) -> Dict:
        """Собирает конфиг и опционально деплоит в игру"""
        self.logger.info('🚀 Starting build...')

        # 1. Дополняем слоты из slots.json (не заменяем!)
        slots_file = self.project_path / 'slots.json'
        if slots_file.exists():
            self._merge_slots(slots_file)
        else:
            self.logger.info('📋 No slots.json found, using default slots only')

        # 2. Сканируем обработчики из src/
        src_dir = self.project_path / 'src'
        if src_dir.exists():
            self._scan_handlers(src_dir)
        else:
            self.logger.warning('src/ directory not found')

        # 3. Строим финальный конфиг
        self._build_config()

        # 4. Деплоим если нужно
        if deploy_to_game and self.deploy_path:
            self._deploy()

        self.logger.info('✅ Build completed successfully!')
        return self.config

    def _merge_slots(self, slots_file: Path):
        """Дополняет слоты из slots.json (мержит с существующими)"""
        self.logger.info(f'📋 Merging slots from {slots_file}')

        try:
            with open(slots_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            added_count = 0
            updated_count = 0

            if isinstance(data, list):
                for slot_name in data:
                    if slot_name not in self.config['slots']:
                        self.config['slots'][slot_name] = {}
                        self.slot_map[slot_name] = {}
                        added_count += 1
                    else:
                        updated_count += 1
                        self.logger.debug(
                            f'   Slot "{slot_name}" already exists, keeping existing config')

            elif isinstance(data, dict):
                for slot_name, slot_config in data.items():
                    # Парсим конфиг слота
                    if isinstance(slot_config, str):
                        slot_data = {'class': slot_config}
                    elif isinstance(slot_config, dict):
                        slot_data = slot_config.copy()
                    else:
                        slot_data = {}

                    if slot_name not in self.config['slots']:
                        # Новый слот
                        self.config['slots'][slot_name] = slot_data
                        self.slot_map[slot_name] = slot_data
                        added_count += 1
                    else:
                        # Существующий слот - дополняем, но не перезаписываем важные поля
                        existing = self.config['slots'][slot_name]
                        # Обновляем только если нет конфликтов или явно указано
                        for key, value in slot_data.items():
                            if key not in existing or existing[key] is None:
                                existing[key] = value
                                updated_count += 1
                            elif key == 'select' and value == 'all':
                                # select=all всегда добавляем если указано
                                existing[key] = value
                            elif key == 'class' and existing.get('class') != value:
                                self.logger.warning(f'   Class mismatch for slot "{slot_name}": '
                                                    f'default={existing.get("class")}, '
                                                    f'from json={value}, keeping default')

            else:
                self.logger.error('Invalid slots.json format')
                return

            self.logger.info(f'   ✅ Merged {len(self.config["slots"])} total slots '
                             f'({added_count} added, {updated_count} updated)')

            # Логируем слоты с select=all
            for name, cfg in self.config['slots'].items():
                if cfg.get('select') == 'all':
                    self.logger.info(
                        f'   🔄 Slot "{name}" will auto-detect all elements')

        except Exception as e:
            self.logger.error(f'❌ Error merging slots.json: {e}')

    def _scan_handlers(self, src_dir: Path):
        """Сканирует папки слотов и собирает обработчики"""
        self.logger.info(f'🔍 Scanning handlers in {src_dir}')

        for slot_dir in src_dir.iterdir():
            if not slot_dir.is_dir():
                continue

            slot_name = slot_dir.name

            if slot_name not in self.slot_map:
                self.logger.warning(
                    f'   Slot "{slot_name}" not found, skipping')
                continue

            if slot_name not in self.handler_groups:
                self.handler_groups[slot_name] = {}

            for lua_file in slot_dir.glob('*.lua'):
                event_info = self._parse_event_from_filename(lua_file.stem)
                if event_info:
                    event_name, args = event_info
                    with open(lua_file, 'r', encoding='utf-8') as f:
                        code = f.read()

                    if event_name not in self.handler_groups[slot_name]:
                        self.handler_groups[slot_name][event_name] = []

                    self.handler_groups[slot_name][event_name].append({
                        'args': args,
                        'code': code,
                        'file': lua_file.name
                    })

                    args_str = f"({', '.join(args)})" if args else ""
                    self.logger.debug(
                        f'   ✅ {slot_name}/{lua_file.name} -> {event_name}{args_str}')

        total_handlers = sum(len(handlers)
                             for handlers in self.handler_groups.values())
        self.logger.info(f'   Found handlers for {len(self.handler_groups)} slots, '
                         f'{total_handlers} events total')

    def _parse_event_from_filename(self, filename: str) -> Optional[Tuple[str, List[str]]]:
        """Парсит имя файла в событие и аргументы автоконф"""
        simple_events = ['onStart', 'onStop', 'onUpdate', 'onFlush']
        if filename in simple_events:
            return (filename, [])

        # Парсим с аргументами в скобках
        match = re.match(
            r'^(onActionStart|onActionStop|onActionLoop|onTimer|onInputText)\((.+)\)$', filename
        )
        if match:
            event_name = match.group(1)
            args_str = match.group(2).strip()
            args = [a.strip() for a in args_str.split(',') if a.strip()]
            return (event_name, args)

        # Если просто имя без скобок
        if filename in ['onActionStart', 'onActionStop', 'onActionLoop', 'onInputText', 'onTimer']:
            return (filename, [])

        return None

    def _build_config(self):
        """Собирает финальный конфиг из собранных данных"""
        self.logger.info('🏗️ Building final config...')

        for slot_name, events in self.handler_groups.items():
            if slot_name not in self.config['handlers']:
                self.config['handlers'][slot_name] = {}

            for event_name, handlers in events.items():
                if len(handlers) == 1:
                    handler = handlers[0]
                    handler_data = {
                        'lua': LiteralScalarString(handler['code'].strip())
                    }
                    
                    # Формируем ключ события
                    event_key = event_name
                    
                    # Добавляем аргументы если они есть
                    if handler['args']:
                        event_key = event_key + '(' + ', '.join(handler['args']) + ')'
                    
                    self.config['handlers'][slot_name][event_key] = handler_data
                else:
                    combined_code = '\n'.join(h['code'].strip() for h in handlers)
                    all_args = []
                    for h in handlers:
                        all_args.extend(h['args'])
                    
                    handler_data = {
                        'lua': LiteralScalarString(combined_code)
                    }
                    
                    # Формируем ключ события
                    event_key = event_name
                    
                    # Добавляем аргументы если они есть
                    if all_args:
                        event_key = event_key + '(' + ', '.join(set(all_args)) + ')'
                    
                    self.config['handlers'][slot_name][event_key] = handler_data

    def _convert_to_literal(self, obj):
        """Рекурсивно преобразует строки с \n в LiteralScalarString"""
        if isinstance(obj, dict):
            return {k: self._convert_to_literal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_to_literal(v) for v in obj]
        elif isinstance(obj, str) and '\n' in obj:
            return LiteralScalarString(obj)
        else:
            return obj

    def _deploy(self):
        if not self.deploy_path:
            self.logger.warning('No deploy path specified')
            return

        self.deploy_path.mkdir(parents=True, exist_ok=True)

        config_name = self.config.get('name', 'config')
        conf_file = self.deploy_path / f'{config_name}.conf'

        # Преобразуем строки с \n в LiteralScalarString
        config = self._convert_to_literal(self.config)

        try:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.width = 4096

            with open(conf_file, 'w', encoding='utf-8') as f:
                f.write(f'# Auto-generated config for {config_name}\n')
                f.write(f'# Generated at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'# Source: {self.project_path}\n\n')
                yaml.dump(config, f)

            slots_count = len(self.config.get('slots', {}))
            handlers_count = sum(len(events) for events in self.config.get('handlers', {}).values())
            self.logger.info(f'✅ Deployed to: {conf_file}')
            self.logger.info(f'   Slots: {slots_count}, Handlers: {handlers_count}')

        except Exception as e:
            self.logger.error(f'❌ Error deploying config: {e}')


class ProjectWatcher(FileSystemEventHandler):
    """Следит за изменениями в проекте"""

    def __init__(self, builder: DUAutoconfBuilder, callback, debounce: float = 1.0):
        self.builder = builder
        self.callback = callback
        self.debounce = debounce
        self.last_build = 0
        self.observer = None
        self.watching = False

    def start(self):
        """Запускает наблюдение"""
        if self.observer:
            return

        self.observer = Observer()
        self.observer.schedule(
            self, str(self.builder.project_path), recursive=True)
        self.observer.start()
        self.watching = True

        self.builder.logger.info(
            f'👀 Watching for changes in {self.builder.project_path}')

    def stop(self):
        """Останавливает наблюдение"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.watching = False
            self.builder.logger.info('👀 Stopped watching')

    def on_any_event(self, event):
        if event.is_directory:
            return

        src_path = Path(event.src_path)
        if src_path.suffix in ['.tmp', '.swp', '~']:
            return

        if any(part.startswith('.') for part in src_path.parts):
            return

        # Отслеживаем только .lua и slots.json
        if not (src_path.suffix == '.json' and src_path.name == 'slots.json') and \
           not (src_path.suffix == '.lua' and 'src' in str(src_path.parent)):
            return

        now = time.time()
        if now - self.last_build < self.debounce:
            return

        self.last_build = now

        rel_path = src_path.relative_to(self.builder.project_path)
        self.builder.logger.info(f'📝 Change detected: {rel_path}')

        if self.callback:
            self.callback()