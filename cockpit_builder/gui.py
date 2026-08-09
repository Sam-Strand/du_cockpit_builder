"""
Dual Universe Autoconf Builder - GUI version
"""

import json
import logging
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime

from .core import DUAutoconfBuilder, ProjectWatcher


class GUILogHandler(logging.Handler):
    """Обработчик логов для вывода в GUI"""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        tag = None
        if record.levelno >= logging.ERROR:
            tag = 'error'
        elif record.levelno >= logging.WARNING:
            tag = 'warning'
        elif record.levelno >= logging.INFO:
            tag = 'info'

        def append():
            self.text_widget.insert(tk.END, msg + '\n', tag)
            self.text_widget.see(tk.END)

        self.text_widget.after(0, append)


class DUBuilderGUI:
    """Главное окно GUI"""

    def __init__(self, root):
        self.root = root
        self.root.title("Dual Universe Autoconf Builder")
        self.root.geometry("900x700")

        # Переменные
        self.project_path = tk.StringVar(value="")
        self.deploy_path = tk.StringVar(
            value="D:/My Dual Universe/Game/data/lua/autoconf/custom/"
        )
        self.is_watching = False

        # Создаем билдер и воркер
        self.builder = None
        self.watcher = None
        self.build_thread = None

        # Настройка интерфейса
        self._setup_ui()

        # Настройка логирования
        self._setup_logging()

        # Загружаем последние настройки
        self._load_settings()

    def _copy_selected(self):
        """Копирует выделенный текст"""
        try:
            selected = self.log_text.selection_get()
            if selected:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
                self.root.update()
                self._update_status("📋 Copied", 'green')
        except tk.TclError:
            self._update_status("⚠️ Nothing selected", 'orange')
    def _setup_ui(self):
        """Создает интерфейс"""
        # Основной фрейм с отступами
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- Заголовок ---
        title_label = ttk.Label(
            main_frame,
            text="🚀 DU Autoconf Builder",
            font=('Arial', 16, 'bold')
        )
        title_label.grid(row=0, column=0, columnspan=3,
                         pady=(0, 15), sticky=tk.W)

        # --- Путь к проекту ---
        ttk.Label(main_frame, text="📁 Project Path:").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        project_entry = ttk.Entry(
            main_frame, textvariable=self.project_path, width=60)
        project_entry.grid(row=1, column=1, sticky=(
            tk.W, tk.E), pady=5, padx=(5, 5))
        ttk.Button(
            main_frame,
            text="Browse...",
            command=self._browse_project
        ).grid(row=1, column=2, pady=5)

        # --- Путь деплоя ---
        ttk.Label(main_frame, text="📂 Deploy Path:").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )
        deploy_entry = ttk.Entry(
            main_frame, textvariable=self.deploy_path, width=60)
        deploy_entry.grid(row=2, column=1, sticky=(
            tk.W, tk.E), pady=5, padx=(5, 5))
        ttk.Button(
            main_frame,
            text="Browse...",
            command=self._browse_deploy
        ).grid(row=2, column=2, pady=5)

        # --- Кнопки управления ---
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=15)

        self.build_btn = ttk.Button(
            button_frame,
            text="🔨 Build Once",
            command=self._build_once,
            width=15
        )
        self.build_btn.pack(side=tk.LEFT, padx=5)

        self.watch_btn = ttk.Button(
            button_frame,
            text="👀 Start Watching",
            command=self._toggle_watch,
            width=15
        )
        self.watch_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            button_frame,
            text="📋 Show Config",
            command=self._show_config,
            width=15
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            button_frame,
            text="🗑️ Clear Log",
            command=self._clear_log,
            width=15
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            button_frame,
            text="📋 Copy",
            command=self._copy_selected,
            width=10
        ).pack(side=tk.LEFT, padx=5)

        # --- Статус ---
        self.status_label = ttk.Label(
            main_frame,
            text="Status: Ready",
            font=('Arial', 10, 'bold'),
            foreground='blue'
        )
        self.status_label.grid(
            row=4, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))

        # --- Разделитель ---
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10
        )

        # --- Лог ---
        log_frame = ttk.LabelFrame(main_frame, text="📋 Build Log", padding="5")
        log_frame.grid(row=6, column=0, columnspan=3,
                       sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        main_frame.rowconfigure(6, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=20,
            font=('Consolas', 9),
            wrap=tk.WORD
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Настройка цветов для лога
        self.log_text.tag_config('error', foreground='red')
        self.log_text.tag_config('warning', foreground='orange')
        self.log_text.tag_config('info', foreground='blue')
        self.log_text.tag_config('success', foreground='green')

        # --- Статистика внизу ---
        stats_frame = ttk.Frame(main_frame)
        stats_frame.grid(row=7, column=0, columnspan=3,
                         sticky=(tk.W, tk.E), pady=10)

        self.stats_label = ttk.Label(
            stats_frame,
            text="Last build: Never | Slots: 0 | Handlers: 0"
        )
        self.stats_label.pack(side=tk.LEFT)

        self.root.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)

    def _setup_logging(self):
        """Настраивает логирование в GUI"""
        gui_handler = GUILogHandler(self.log_text)
        gui_handler.setFormatter(
            logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S')
        )

        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(gui_handler)

        # Отключаем вывод в консоль
        for handler in logger.handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                logger.removeHandler(handler)

    def _browse_project(self):
        """Выбор папки проекта"""
        path = filedialog.askdirectory(title="Select Project Folder")
        if path:
            self.project_path.set(path)
            self._save_settings()

    def _browse_deploy(self):
        """Выбор папки деплоя"""
        path = filedialog.askdirectory(title="Select Deploy Folder")
        if path:
            self.deploy_path.set(path)
            self._save_settings()

    def _load_settings(self):
        """Загружает сохраненные настройки"""
        settings_file = Path.home() / '.du_builder_settings.json'
        if settings_file.exists():
            try:
                with open(settings_file, 'r') as f:
                    settings = json.load(f)
                    if 'project_path' in settings:
                        self.project_path.set(settings['project_path'])
                    if 'deploy_path' in settings:
                        self.deploy_path.set(settings['deploy_path'])
            except:
                pass

    def _save_settings(self):
        """Сохраняет настройки"""
        settings_file = Path.home() / '.du_builder_settings.json'
        try:
            with open(settings_file, 'w') as f:
                json.dump({
                    'project_path': self.project_path.get(),
                    'deploy_path': self.deploy_path.get()
                }, f)
        except:
            pass

    def _build_once(self):
        """Однократная сборка"""
        if not self.project_path.get():
            messagebox.showerror("Error", "Please select a project path")
            return

        self._update_status("Building...", 'orange')
        self._log_message("=" * 50, 'info')
        self._log_message("🚀 Starting build...", 'info')

        if self.build_thread and self.build_thread.is_alive():
            self._log_message("⏳ Build already in progress...", 'warning')
            return

        self.build_thread = threading.Thread(
            target=self._do_build, daemon=True)
        self.build_thread.start()

    def _do_build(self):
        """Выполняет сборку в отдельном потоке"""
        try:
            self.builder = DUAutoconfBuilder(
                self.project_path.get(),
                self.deploy_path.get(),
                logger=logging.getLogger()
            )

            config = self.builder.build(deploy_to_game=True)

            slots = len(config.get('slots', {}))
            handlers = sum(len(events)
                           for events in config.get('handlers', {}).values())

            self.root.after(0, lambda: self._update_stats(slots, handlers))
            self.root.after(0, lambda: self._update_status(
                "✅ Build successful", 'green'))

        except Exception as e:
            self.root.after(0, lambda: self._update_status(
                f"❌ Build failed: {e}", 'red'))
            self.root.after(0, lambda: self._log_message(
                f"❌ Error: {e}", 'error'))

    def _toggle_watch(self):
        """Включает/выключает режим наблюдения"""
        if not self.is_watching:
            if not self.project_path.get():
                messagebox.showerror("Error", "Please select a project path")
                return
            self._start_watching()
        else:
            self._stop_watching()

    def _start_watching(self):
        """Запускает наблюдение за изменениями"""
        self._log_message("👀 Starting file watcher...", 'info')

        self.builder = DUAutoconfBuilder(
            self.project_path.get(),
            self.deploy_path.get(),
            logger=logging.getLogger()
        )

        self.watcher = ProjectWatcher(self.builder, self._on_change)
        self.watcher.start()
        self.is_watching = True

        self.watch_btn.config(text="⏹️ Stop Watching")
        self._update_status("👀 Watching for changes...", 'blue')

        self._build_once()

    def _stop_watching(self):
        """Останавливает наблюдение"""
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.is_watching = False

        self.watch_btn.config(text="👀 Start Watching")
        self._update_status("Stopped watching", 'blue')
        self._log_message("👀 Stopped watching", 'info')

    def _on_change(self):
        """Callback при изменении файлов - пересборка"""
        self._build_once()

    def _show_config(self):
        """Показывает сгенерированный конфиг"""
        if not self.builder:
            messagebox.showinfo("Info", "Build first to generate config")
            return

        try:
            config_name = self.builder.config.get('name', 'config')
            conf_file = Path(self.deploy_path.get()) / f'{config_name}.conf'

            if not conf_file.exists():
                messagebox.showinfo(
                    "Info", "Config file not found. Build first.")
                return

            view_window = tk.Toplevel(self.root)
            view_window.title(f"Config: {conf_file.name}")
            view_window.geometry("700x500")

            text = scrolledtext.ScrolledText(
                view_window, font=('Consolas', 10))
            text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            with open(conf_file, 'r', encoding='utf-8') as f:
                content = f.read()
                text.insert('1.0', content)
                text.config(state=tk.DISABLED)

        except Exception as e:
            messagebox.showerror("Error", f"Could not open config: {e}")

    def _clear_log(self):
        """Очищает лог"""
        self.log_text.delete('1.0', tk.END)

    def _log_message(self, message, tag=None):
        """Логирует сообщение в GUI"""
        self.log_text.insert(tk.END, message + '\n', tag)
        self.log_text.see(tk.END)

    def _update_status(self, message, color='blue'):
        """Обновляет статус"""
        self.status_label.config(text=f"Status: {message}", foreground=color)
        self.root.update_idletasks()

    def _update_stats(self, slots, handlers):
        """Обновляет статистику"""
        now = datetime.now().strftime("%H:%M:%S")
        self.stats_label.config(
            text=f"Last build: {now} | Slots: {slots} | Handlers: {handlers}"
        )

    def on_closing(self):
        """Обработка закрытия окна"""
        if self.is_watching:
            self._stop_watching()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = DUBuilderGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == '__main__':
    main()
