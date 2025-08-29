import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
from googletrans import Translator, LANGUAGES
from concurrent.futures import ThreadPoolExecutor
import asyncio
import time

# --- Configuration ---
LANG_DIR = 'languages'

# --- Main Application Class ---
class TranslationEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Language Editor (v1.0.0)") 
        self.geometry("1200x750")

        self.style = ttk.Style(self)
        self.style.map("Custom.Treeview", background=[('selected', '#0078D7')])
        
        self.translator = Translator()
        self.source_data = {}
        self.target_data = {}
        self.available_langs = []
        
        self.current_source_lang = tk.StringVar()
        self.current_target_lang = tk.StringVar()
        
        self.thread_executor = ThreadPoolExecutor(max_workers=1)
        
        self.create_widgets()
        self.create_context_menu()
        
        startup_ok = self.scan_for_languages()
        if startup_ok:
            self.populate_language_selectors()
            self.on_source_language_change(None)
        else:
            self.after(100, self.show_startup_error)
            
    def show_startup_error(self):
        self.show_centered_message("Error", f"No .json language files found in '{os.path.abspath(LANG_DIR)}'.\n\nThe application will now close.")
        self.destroy()

    def create_widgets(self):
        main_pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        left_frame = ttk.Frame(main_pane, width=250)
        main_pane.add(left_frame, minsize=250)
        lang_frame = ttk.LabelFrame(left_frame, text="Language Selection")
        lang_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(lang_frame, text="Source Language:").pack(fill=tk.X, padx=5, pady=(5,0))
        self.source_lang_selector = ttk.Combobox(lang_frame, textvariable=self.current_source_lang, state="readonly")
        self.source_lang_selector.pack(fill=tk.X, padx=5, pady=5)
        self.source_lang_selector.bind("<<ComboboxSelected>>", self.on_source_language_change)
        ttk.Label(lang_frame, text="Target Language:").pack(fill=tk.X, padx=5, pady=(5,0))
        self.target_lang_selector = ttk.Combobox(lang_frame, textvariable=self.current_target_lang, state="readonly")
        self.target_lang_selector.pack(fill=tk.X, padx=5, pady=5)
        self.target_lang_selector.bind("<<ComboboxSelected>>", self.on_target_language_change)
        action_frame = ttk.LabelFrame(left_frame, text="Actions")
        action_frame.pack(fill=tk.X, padx=5, pady=10)
        ttk.Button(action_frame, text="Auto-Translate All", command=self.auto_translate_all).pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(action_frame, text="Save Files", command=self.save_files).pack(fill=tk.X, padx=5, pady=5)
        right_frame = ttk.Frame(main_pane)
        main_pane.add(right_frame)
        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("key", "source_val", "target_val"), show="headings", style="Custom.Treeview", selectmode='extended')
        self.tree.heading("key", text="Key")
        self.tree.heading("source_val", text="Source")
        self.tree.heading("target_val", text="Target")
        self.tree.column("key", width=250, stretch=tk.NO)
        self.tree.column("source_val", width=350)
        self.tree.column("target_val", width=350)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.on_double_click_edit)
        self.tree.bind("<Button-3>", self.show_context_menu)

    def create_context_menu(self):
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Translate Selection", command=self.translate_selection)

    def show_context_menu(self, event):
        selection = self.tree.selection()
        if not selection:
            self.context_menu.entryconfigure("Translate Selection", state="disabled")
        else:
            item_id = self.tree.identify_row(event.y)
            if item_id and item_id not in selection:
                self.tree.selection_set(item_id)
            self.context_menu.entryconfigure("Translate Selection", state="normal")
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def translate_selection(self):
        selected_keys = self.tree.selection()
        if not selected_keys: return
        source_lang = self.get_lang_code(self.current_source_lang.get())
        target_lang = self.get_lang_code(self.current_target_lang.get())
        self.show_progress_popup(max_val=len(selected_keys), title="Translating Selection")
        self.thread_executor.submit(self.translation_worker, selected_keys, source_lang, target_lang)

    def scan_for_languages(self):
        files = [f for f in os.listdir(LANG_DIR) if f.endswith('.json')]
        if not files: return False
        lang_codes = [f.split('.')[0] for f in files if f.split('.')[0] in LANGUAGES]
        self.available_langs = sorted([f"{LANGUAGES[code].capitalize()} ({code})" for code in lang_codes])
        return True

    def populate_language_selectors(self):
        self.source_lang_selector['values'] = self.available_langs
        if 'Spanish (es)' in self.available_langs: self.current_source_lang.set('English (en)')
        elif self.available_langs: self.current_source_lang.set(self.available_langs[0])
        self.update_target_selector()

    def update_target_selector(self):
        source_selection = self.current_source_lang.get()
        target_list = [lang for lang in self.available_langs if lang != source_selection]
        self.target_lang_selector['values'] = target_list
        if target_list:
            current_target = self.current_target_lang.get()
            if current_target in target_list and current_target != source_selection: self.current_target_lang.set(current_target)
            else: self.current_target_lang.set(target_list[0])
        else: self.current_target_lang.set('')

    def get_lang_code(self, selection_string):
        if selection_string and '(' in selection_string: return selection_string[selection_string.rfind('(') + 1:-1]
        return ""

    def load_source_file(self):
        lang_code = self.get_lang_code(self.current_source_lang.get())
        if not lang_code: return
        file_path = os.path.join(LANG_DIR, f"{lang_code}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f: self.source_data = json.load(f)
            self.tree.heading("source_val", text=f"Source ({lang_code.upper()})")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.source_data = {}
            self.show_centered_message("Error", f"Could not load source file '{file_path}':\n{e}")

    def load_target_file(self):
        lang_code = self.get_lang_code(self.current_target_lang.get())
        if not lang_code: 
            self.target_data = {}; self.tree.heading("target_val", text="Target"); return
        file_path = os.path.join(LANG_DIR, f"{lang_code}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f: self.target_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): self.target_data = {}
        self.tree.heading("target_val", text=f"Target ({lang_code.upper()})")

    def populate_treeview(self):
        selection = self.tree.selection()
        for item in self.tree.get_children(): self.tree.delete(item)
        for key, source_value in sorted(self.source_data.items()):
            target_value = self.target_data.get(key, "")
            self.tree.insert("", "end", iid=key, values=(key, source_value, target_value))
        if selection: self.tree.selection_set(selection)
        self.flash_refresh()

    def flash_refresh(self):
        self.style.configure("Custom.Treeview", background="#E0F0FF")
        self.after(250, lambda: self.style.configure("Custom.Treeview", background="white"))

    def on_source_language_change(self, event):
        self.update_target_selector(); self.load_source_file(); self.load_target_file(); self.populate_treeview()

    def on_target_language_change(self, event):
        self.load_target_file(); self.populate_treeview()

    def on_double_click_edit(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        item_id = self.tree.focus()
        column = self.tree.identify_column(event.x)
        col_index = int(column.replace('#', '')) - 1
        if col_index == 0: return
        current_value = self.tree.item(item_id, "values")[col_index]
        entry = ttk.Entry(self.tree, justify="left")
        x, y, width, height = self.tree.bbox(item_id, column)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, current_value)
        entry.focus_force()
        def save_edit(e):
            new_value = entry.get()
            entry.destroy()
            current_values = list(self.tree.item(item_id, "values"))
            current_values[col_index] = new_value
            self.tree.item(item_id, values=tuple(current_values))
            key = item_id
            if col_index == 1: self.source_data[key] = new_value
            elif col_index == 2: self.target_data[key] = new_value
        entry.bind("<Return>", save_edit); entry.bind("<FocusOut>", save_edit); entry.bind("<Escape>", lambda e: entry.destroy())

    def auto_translate_all(self):
        source_lang, target_lang = (self.get_lang_code(self.current_source_lang.get()), self.get_lang_code(self.current_target_lang.get()))
        if not source_lang or not target_lang:
            self.show_centered_message("Warning", "Please select both a source and a target language.")
            return
        if not self.show_centered_askyesno("Confirm", f"This will overwrite all existing translations for '{target_lang.upper()}'.\nAre you sure you want to proceed?"):
            return
        all_keys = list(self.source_data.keys())
        self.show_progress_popup(max_val=len(all_keys))
        self.thread_executor.submit(self.translation_worker, all_keys, source_lang, target_lang)

    def translation_worker(self, keys_to_translate, source_lang, target_lang):
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        count = 0; self.any_errors_occured = False
        for key in keys_to_translate:
            source_text = self.source_data.get(key)
            if not source_text: continue
            translated_text, success = "", False
            for attempt in range(3):
                try:
                    coro = self.translator.translate(source_text, src=source_lang, dest=target_lang)
                    translated = loop.run_until_complete(coro)
                    translated_text, success = translated.text, True
                    break
                except Exception as e:
                    print(f"Error translating '{key}' on attempt {attempt + 1}: {e}"); time.sleep(0.5)
            if not success: self.any_errors_occured = True; translated_text = "ERROR"
            self.target_data[key] = translated_text
            self.after(0, self.update_single_row, key, translated_text)
            count += 1
            self.after(0, self.update_progress, count)
            time.sleep(0.2)
        loop.close()
        self.after(0, self.close_progress_popup)

    def update_single_row(self, key, text):
        self.tree.set(key, column="target_val", value=text)

    def show_progress_popup(self, mode='determinate', max_val=100, title="Translating...", label_text="Translating, please wait..."):
        self.progress_popup = tk.Toplevel(self); self.progress_popup.title(title)
        popup_width, popup_height = 300, 100
        main_x, main_y = self.winfo_x(), self.winfo_y()
        main_width, main_height = self.winfo_width(), self.winfo_height()
        center_x, center_y = main_x + (main_width // 2) - (popup_width // 2), main_y + (main_height // 2) - (popup_height // 2)
        self.progress_popup.geometry(f'{popup_width}x{popup_height}+{center_x}+{center_y}')
        self.progress_popup.transient(self); self.progress_popup.grab_set()
        ttk.Label(self.progress_popup, text=label_text).pack(pady=10)
        self.progress_bar = ttk.Progressbar(self.progress_popup, orient="horizontal", length=280, mode=mode)
        self.progress_bar.pack(pady=5)
        if mode == 'determinate': self.progress_bar.configure(maximum=max_val)
        else: self.progress_bar.start(10)

    def update_progress(self, value):
        if hasattr(self, 'progress_bar') and self.progress_bar.winfo_exists(): self.progress_bar['value'] = value

    def close_progress_popup(self):
        if hasattr(self, 'progress_popup'):
            self.progress_popup.destroy()
            self.flash_refresh()
            if self.any_errors_occured:
                self.show_centered_message("Finished", "Translation complete with some errors!")
            else:
                self.show_centered_message("Success", "Translation complete!")

    def _center_popup(self, popup):
        self.update_idletasks()
        popup.update_idletasks()
        width = popup.winfo_reqwidth()
        height = popup.winfo_reqheight()
        main_x, main_y = self.winfo_x(), self.winfo_y()
        main_width, main_height = self.winfo_width(), self.winfo_height()
        center_x = main_x + (main_width // 2) - (width // 2)
        center_y = main_y + (main_height // 2) - (height // 2)
        popup.geometry(f'+{center_x}+{center_y}')

    def show_centered_message(self, title, message):
        msg_box = tk.Toplevel(self); msg_box.title(title)
        
        ttk.Label(msg_box, text=message, padding=(20, 20), wraplength=350, justify='center').pack(expand=True, fill='both')
        
        # --- THIS IS THE FIX ---
        # Use a standard tk.Button for reliable coloring
        ok_button = tk.Button(msg_box, text="OK", command=msg_box.destroy, 
                              background="#0078D7", foreground="white",
                              activebackground="#005a9e", activeforeground="white",
                              font=('Helvetica', 9, 'bold'), borderwidth=0,
                              padx=10, pady=5)
        ok_button.pack(pady=10)
        
        msg_box.transient(self); msg_box.grab_set()
        self._center_popup(msg_box)
        self.wait_window(msg_box)

    def show_centered_askyesno(self, title, message):
        dialog = tk.Toplevel(self); dialog.title(title)
        result = tk.BooleanVar(value=False)
        ttk.Label(dialog, text=message, padding=(20, 20), wraplength=350, justify='center').pack(expand=True, fill='both')
        button_frame = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        button_frame.pack()
        
        def set_result_and_close(value):
            result.set(value)
            dialog.destroy()

        # --- THIS IS THE FIX ---
        # Use standard tk.Button for reliable coloring
        yes_button = tk.Button(button_frame, text="Yes", command=lambda: set_result_and_close(True),
                               background="#0078D7", foreground="white",
                               activebackground="#005a9e", activeforeground="white",
                               font=('Helvetica', 9, 'bold'), borderwidth=0,
                               padx=10, pady=5)
        yes_button.pack(side='left', padx=10)
        
        no_button = tk.Button(button_frame, text="No", command=lambda: set_result_and_close(False),
                              font=('Helvetica', 9), padx=10, pady=5) # Standard look for "No"
        no_button.pack(side='left', padx=10)
        
        dialog.transient(self); dialog.grab_set()
        self._center_popup(dialog)
        self.wait_window(dialog)
        return result.get()

    def save_files(self):
        source_lang, target_lang = (self.get_lang_code(self.current_source_lang.get()), self.get_lang_code(self.current_target_lang.get()))
        if not source_lang or not target_lang:
            self.show_centered_message("Error", "No languages selected.")
            return
        saved_files = []
        try:
            source_filepath = os.path.join(LANG_DIR, f"{source_lang}.json")
            with open(source_filepath, 'w', encoding='utf-8') as f: json.dump(self.source_data, f, ensure_ascii=False, indent=2)
            saved_files.append(source_filepath)
            target_filepath = os.path.join(LANG_DIR, f"{target_lang}.json")
            with open(target_filepath, 'w', encoding='utf-8') as f: json.dump(self.target_data, f, ensure_ascii=False, indent=2)
            saved_files.append(target_filepath)
            message_text = f"Files saved successfully:\n- {saved_files[0]}\n- {saved_files[1]}"
            self.show_centered_message("Success", message_text)
        except Exception as e:
            self.show_centered_message("Error", f"Failed to save files: {e}")

if __name__ == "__main__":
    app = TranslationEditor()
    # --- THIS IS THE FIX ---
    # The unreliable ttk style is no longer needed
    app.mainloop()