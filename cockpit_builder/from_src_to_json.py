# cockpit_builder/from_src_to_json.py

import json
import re
import time
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional, Any, Tuple
from watchdog.events import FileSystemEventHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class DUConfigBuilder:
    '''Сборщик конфигурации из файловой структуры'''

    SYSTEM_SLOTS = {
        'unit': '-1',
        'construct': '-2',
        'player': '-3',
        'system': '-4',
        'core': '0',
        'library': '-5'
    }

    slot_counter = 1

    def __init__(self, project_path: str, deploy_path: str = None):
        """
        Args:
            project_path: путь к проекту
            deploy_path: путь для деплоя inGame (например, E:/Dual Universe/Game/)
        """
        self.project_path = Path(project_path)
        self.deploy_path = Path(deploy_path) if deploy_path else None
        self.config = {
            'slots': {},
            'handlers': [],
            'methods': [],
            'events': []
        }
        self.slot_map = {}
        self.slot_configs = {}

    def build(self, deploy_to_game: bool = False) -> Dict:
        """
        Собирает конфиг и опционально деплоит inGame
        
        Args:
            deploy_to_game: если True - копирует inGame в указанный путь
        """
        self._add_system_slots()

        slots_file = self.project_path / 'slots.json'
        if slots_file.exists():
            self._parse_slots_json(slots_file)
        else:
            logger.warning(f'slots.json not found in {self.project_path}')

        src_dir = self.project_path / 'src'
        if src_dir.exists():
            self._scan_handlers(src_dir)
        else:
            logger.warning(f'src/ directory not found in {self.project_path}')

        # Деплоим inGame если нужно
        if deploy_to_game and self.deploy_path:
            self._deploy_ingame()

        return self.config

    def _deploy_ingame(self):
        """Копирует содержимое inGame в указанный путь"""
        ingame_dir = self.project_path / 'src' / 'inGame'
        
        if not ingame_dir.exists():
            logger.info('   No inGame directory found in src/')
            return
        
        if not self.deploy_path:
            logger.warning('   No deploy path specified, skipping inGame deployment')
            return
        
        logger.info(f'📁 Deploying inGame from {ingame_dir} to {self.deploy_path}')
        
        # Создаем целевую папку если её нет
        self.deploy_path.mkdir(parents=True, exist_ok=True)
        
        copied_count = 0
        
        # Копируем всё содержимое inGame
        for item in ingame_dir.rglob('*'):
            if item.is_file():
                # Вычисляем относительный путь от inGame
                rel_path = item.relative_to(ingame_dir)
                target_file = self.deploy_path / rel_path
                
                # Создаем папки для файла
                target_file.parent.mkdir(parents=True, exist_ok=True)
                
                try:
                    shutil.copy2(item, target_file)
                    copied_count += 1
                    logger.debug(f'   ✅ Copied: {rel_path} -> {target_file}')
                except Exception as e:
                    logger.error(f'   ❌ Error copying {rel_path}: {e}')
        
        logger.info(f'   ✅ Deployed {copied_count} files from inGame to {self.deploy_path}')

    def _add_system_slots(self):
        for name, key in self.SYSTEM_SLOTS.items():
            self.config['slots'][key] = {
                'name': name,
                'type': {'methods': [], 'events': []}
            }
            self.slot_map[name] = key

    def _parse_slots_json(self, slots_file: Path):
        logger.info(f'Parsing {slots_file}')

        try:
            with open(slots_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, list):
                for slot_name in data:
                    self._add_slot_from_config(slot_name, {})
            elif isinstance(data, dict):
                for slot_name, slot_config in data.items():
                    if isinstance(slot_config, str):
                        slot_config = {'class': slot_config}
                    elif slot_config is None:
                        slot_config = {}
                    self._add_slot_from_config(slot_name, slot_config)
            else:
                logger.error(f'Invalid slots.json format')
                return

        except Exception as e:
            logger.error(f'Error parsing slots.json: {e}')

    def _add_slot_from_config(self, name: str, config: Dict[str, Any]):
        if name in self.SYSTEM_SLOTS:
            key = self.SYSTEM_SLOTS[name]
        else:
            key = str(self.slot_counter)
            self.slot_counter += 1

        slot_entry = {
            'name': name,
            'type': {
                'methods': config.get('methods', []),
                'events': config.get('events', [])
            }
        }

        if 'class' in config and name not in self.SYSTEM_SLOTS:
            slot_entry['type']['class'] = config['class']

        if 'select' in config:
            slot_entry['type']['select'] = config['select']

        if 'group' in config:
            slot_entry['type']['group'] = config['group']

        if 'category' in config:
            slot_entry['type']['category'] = config['category']

        self.config['slots'][key] = slot_entry
        self.slot_map[name] = key

        logger.debug(
            f'Added slot: {name} (key: {key}, class: {config.get("class")}, select: {config.get("select")})')

    def _scan_handlers(self, src_dir: Path):
        logger.info(f'Scanning handlers in {src_dir}')

        handler_count = 0

        # Сканируем папки слотов, но пропускаем inGame
        for slot_dir in src_dir.iterdir():
            if not slot_dir.is_dir():
                continue
            
            # Пропускаем inGame папку - она обрабатывается отдельно
            if slot_dir.name == 'inGame':
                continue

            slot_name = slot_dir.name
            slot_key = self.slot_map.get(slot_name)

            if not slot_key:
                logger.warning(
                    f'Slot "{slot_name}" not found in slots.json, skipping')
                continue

            for lua_file in slot_dir.glob('*.lua'):
                handler = self._parse_lua_handler(lua_file, slot_key)
                if handler:
                    self.config['handlers'].append(handler)
                    handler_count += 1

        logger.info(f'Found {handler_count} handlers total')

    def _parse_lua_handler(self, lua_file: Path, slot_key: str) -> Optional[Dict]:
        filename = lua_file.stem

        result = self._parse_signature_and_args(filename)
        if not result:
            return None

        signature, args = result

        with open(lua_file, 'r', encoding='utf-8') as f:
            code = f.read()

        return {
            'key': str(len(self.config['handlers'])),
            'filter': {
                'slotKey': slot_key,
                'signature': signature,
                'args': args
            },
            'code': code
        }

    def _parse_signature_and_args(self, filename: str) -> Optional[Tuple[str, list]]:
        """Парсит сигнатуру и аргументы из имени файла"""

        simple_patterns = {
            'onStart': 'onStart()',
            'onStop': 'onStop()',
            'onUpdate': 'onUpdate()',
            'onFlush': 'onFlush()',
        }

        if filename in simple_patterns:
            return simple_patterns[filename], []

        match = re.match(
            r'^(onActionStart|onActionStop|onActionLoop|onTimer)\((.+)\)$', filename)
        if match:
            func_name = match.group(1)
            arg_value = match.group(2)
            return f'{func_name}({arg_value})', [{'value': arg_value}]

        return None


class ProjectWatcher(FileSystemEventHandler):
    '''Следит за изменениями в проекте'''

    def __init__(self, builder: DUConfigBuilder, output_path: Path, debounce: float = 1.0):
        self.builder = builder
        self.output_path = output_path
        self.debounce = debounce
        self.last_build = 0

    def on_any_event(self, event):
        if event.is_directory:
            return

        if Path(event.src_path).suffix in ['.tmp', '.swp', '~']:
            return

        if Path(event.src_path) == self.output_path:
            return

        now = time.time()
        if now - self.last_build < self.debounce:
            return

        self.last_build = now

        if hasattr(event, 'event_type') and event.event_type in ['modified', 'created', 'moved']:
            logger.info(f'Change detected: {Path(event.src_path).name}')
            self._build_and_save()

    def _build_and_save(self):
        try:
            config = self.builder.build(deploy_to_game=True)  # Автоматический деплой
            with open(self.output_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f'✅ Config saved: {self.output_path}')
            logger.info(
                f'   Slots: {len(config["slots"])}, Handlers: {len(config["handlers"])}')
        except Exception as e:
            logger.error(f'❌ Build failed: {e}')