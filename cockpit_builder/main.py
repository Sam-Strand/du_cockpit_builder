import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from watchdog.events import FileSystemEventHandler


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

class DUConfigBuilder:
    '''Сборщик конфигурации для Dual Universe'''
    
    # Системные слоты с фиксированными ключами
    SYSTEM_SLOTS = {
        'unit': '-1',
        'construct': '-2',
        'player': '-3',
        'system': '-4',
        'core': '0',
        'library': '-5'
    }

    slot_counter = 1
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.config = {
            'slots': {},
            'handlers': [],
            'methods': [],
            'events': []
        }
        self.slot_map = {}  # имя слота -> ключ
        self.slot_configs = {}  # полная конфигурация слотов
        
    def build(self) -> Dict:
        '''Собирает полный конфиг'''
        # 1. Добавляем системные слоты
        self._add_system_slots()
        
        # 2. Парсим slots.json
        slots_file = self.project_path / 'slots.json'
        if slots_file.exists():
            self._parse_slots_json(slots_file)
        else:
            logger.warning(f'slots.json not found in {self.project_path}')
            # Пробуем старый формат slots.txt для обратной совместимости
            slots_txt = self.project_path / 'slots.txt'
            if slots_txt.exists():
                logger.info('Found slots.txt, converting...')
                self._parse_slots_txt(slots_txt)
        
        # 3. Сканируем src/ папку для обработчиков
        src_dir = self.project_path / 'src'
        if src_dir.exists():
            self._scan_handlers(src_dir)
        else:
            logger.warning(f'src/ directory not found in {self.project_path}')
        
        return self.config
    
    def _add_system_slots(self):
        '''Добавляет системные слоты'''
        for name, key in self.SYSTEM_SLOTS.items():
            self.config['slots'][key] = {
                'name': name,
                'type': {'methods': [], 'events': []}
            }
            self.slot_map[name] = key
    
    def _parse_slots_json(self, slots_file: Path):
        '''Парсит slots.json'''
        logger.info(f'Parsing {slots_file}')
        
        try:
            with open(slots_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Поддерживаем два формата:
            # 1. Прямой список слотов
            # 2. Объект с секциями
            if isinstance(data, list):
                slots_data = {str(i): slot for i, slot in enumerate(data)}
            elif isinstance(data, dict):
                slots_data = data
            else:
                logger.error(f'Invalid slots.json format: expected list or dict')
                return
            
            for slot_name, slot_config in slots_data.items():
                # Если slot_name это число, используем его как имя
                if isinstance(slot_config, str):
                    # Простой формат: 'slot_name': 'ClassName'
                    slot_config = {'class': slot_config}
                
                self._add_slot_from_config(slot_name, slot_config)
                
        except json.JSONDecodeError as e:
            logger.error(f'Error parsing slots.json: {e}')
        except Exception as e:
            logger.error(f'Error reading slots.json: {e}')
    
    def _parse_slots_txt(self, slots_file: Path):
        '''Парсит старый формат slots.txt (для обратной совместимости)'''
        logger.info(f'Parsing {slots_file} (legacy format)')
        
        with open(slots_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Убираем комментарии
        lines = [line.strip() for line in content.split('\n') 
                if line.strip() and not line.strip().startswith('#')]
        
        current_slot = None
        slot_config = {}
        
        for line in lines:
            if ':' in line and not line.startswith((' ', '\t')):
                if current_slot and slot_config:
                    self._add_slot_from_config(current_slot, slot_config)
                
                parts = line.split(':', 1)
                current_slot = parts[0].strip()
                slot_config = {}
                
                if len(parts) > 1 and parts[1].strip():
                    slot_config['class'] = parts[1].strip()
            elif line.startswith((' ', '\t')) and current_slot:
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    # Пробуем преобразовать значение
                    if value.lower() in ['true', 'false']:
                        value = value.lower() == 'true'
                    elif value.isdigit():
                        value = int(value)
                    slot_config[key] = value
        
        if current_slot and slot_config:
            self._add_slot_from_config(current_slot, slot_config)
    
    def _add_slot_from_config(self, name: str, config: Dict[str, Any]):
        '''Добавляет слот из конфигурации'''
        # Проверяем системный слот
        if name in self.SYSTEM_SLOTS:
            key = self.SYSTEM_SLOTS[name]
            logger.debug(f'System slot: {name} -> {key}')
        else:
            key = str(self.slot_counter)
            self.slot_counter += 1
        
        slot_type = config.get('type')
        
        # Формируем конфиг слота
        slot_entry = {
            'name': name,
            'type': {
                'methods': config.get('methods', []),
                'events': config.get('events', [])
            }
        }
        
        # Добавляем дополнительные параметры если есть
        extra_params = ['select', 'group', 'category']
        for param in extra_params:
            if param in config:
                slot_entry['type'][param] = config[param]
        
        # Если указан тип, добавляем его
        if slot_type:
            slot_entry['type']['class'] = slot_type
        
        # Сохраняем
        self.config['slots'][key] = slot_entry
        self.slot_map[name] = key
        self.slot_configs[name] = config
        
        logger.debug(f'Added slot: {name} (key: {key}, type: {slot_type})')
    
    def _scan_handlers(self, src_dir: Path):
        '''Сканирует папку src для поиска обработчиков'''
        logger.info(f'Scanning handlers in {src_dir}')
        
        handler_count = 0
        
        # Проходим по всем папкам (каждая папка = слот)
        for slot_dir in src_dir.iterdir():
            if not slot_dir.is_dir():
                continue
            
            slot_name = slot_dir.name
            slot_key = self.slot_map.get(slot_name)
            
            if not slot_key:
                logger.warning(f'Slot '{slot_name}' not found in slots.json, skipping')
                continue
            
            # Сканируем Lua файлы в папке слота
            for lua_file in slot_dir.glob('*.lua'):
                handler = self._parse_lua_handler(lua_file, slot_key)
                if handler:
                    self.config['handlers'].append(handler)
                    handler_count += 1
                    logger.debug(f'Added handler: {lua_file.name} -> {handler["filter"]["signature"]}')
        
        logger.info(f'Found {handler_count} handlers total')
    
    def _parse_lua_handler(self, lua_file: Path, slot_key: str) -> Optional[Dict]:
        '''Парсит Lua файл и определяет тип обработчика'''
        filename = lua_file.stem
        
        # Парсим сигнатуру из имени файла
        signature, args = self._parse_signature_and_args(filename)
        if not signature:
            return None
        
        # Читаем код
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
    
    def _parse_signature_and_args(self, filename: str) -> tuple:
        '''Парсит сигнатуру и аргументы из имени файла'''
        patterns = [
            (r'^onStart$', 'onStart()', []),
            (r'^onStop$', 'onStop()', []),
            (r'^onUpdate$', 'onUpdate()', []),
            (r'^onFlush$', 'onFlush()', []),
            (r'^onActionStart\((.+)\)$', lambda m: ('onActionStart', [m.group(1)])),
            (r'^onActionStop\((.+)\)$', lambda m: ('onActionStop', [m.group(1)])),
            (r'^onActionLoop\((.+)\)$', lambda m: ('onActionLoop', [m.group(1)])),
            (r'^onTimer\((.+)\)$', lambda m: ('onTimer', [m.group(1)])),
        ]
        
        for pattern, signature, args in patterns:
            match = re.match(pattern, filename)
            if match:
                if callable(signature):
                    sig_name, args_list = signature(match)
                    return f'{sig_name}()', [{'value': arg} for arg in args_list]
                return signature, [{'value': arg} for arg in args]
        
        return None, None

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
        
        # Игнорируем временные файлы
        if Path(event.src_path).suffix in ['.tmp', '.swp', '~']:
            return
        
        # Игнорируем выходной файл
        if Path(event.src_path) == self.output_path:
            return
        
        # Дебаунс
        now = time.time()
        if now - self.last_build < self.debounce:
            return
        
        self.last_build = now
        
        if hasattr(event, 'event_type') and event.event_type in ['modified', 'created', 'moved']:
            logger.info(f'Change detected: {Path(event.src_path).name}')
            self._build_and_save()
    
    def _build_and_save(self):
        '''Собирает и сохраняет конфиг'''
        try:
            config = self.builder.build()
            with open(self.output_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f'✅ Config saved: {self.output_path}')
            logger.info(f'   Slots: {len(config["slots"])}, Handlers: {len(config["handlers"])}')
        except Exception as e:
            logger.error(f'❌ Build failed: {e}')
