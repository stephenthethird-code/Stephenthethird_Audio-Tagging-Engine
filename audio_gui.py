# test# audio_gui.py

import os
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

from converter_core import convert_audio, get_extension


SUPPORTED_OUTPUT_FORMATS = [
    "wav",
    "mp3",
    "m4a",
    "flac",
    "ogg",
    "aac",
]


class AudioConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Audio Converter")
        self.geometry("500x260")
        self.resizable(False, False)

        self.input_path = None

        self.create_widgets()
        self.setup_drag_and_drop_hint()

    def create_widgets(self):
        padding = {"padx": 12, "pady": 8}

        # Instruction label
        self.label_info = tk.Label(
            self,
            text="Drag & drop an audio file here, or click 'Browse' to select one.",
            wraplength=460,
            justify="left",
        )
        self.label_info.pack(**padding)

        # Frame for drop area + browse button
        frame_top = tk.Frame(self)
        frame_top.pack(fill="x", **padding)

        # Drop area (just a big label; we handle click to open dialog)
        self.drop_area = tk.Label(
            frame_top,
            text="Drop file here",
            relief="ridge",
            borderwidth=2,
            width=40,
            height=4,
            bg="#f5f5f5",
        )
        self.drop_area.pack(side="left", expand=True, fill="both", padx=(0, 8))

        # Let clicking the drop area open file dialog (drag-and-drop is extra)
        self.drop_area.bind("<Button-1>", lambda e: self.browse_file())

        # Browse button
        self.btn_browse = tk.Button(frame_top, text="Browse…", command=self.browse_file)
        self.btn_browse.pack(side="right")

        # Selected file label
        self.label_file = tk.Label(self, text="No file selected", fg="#555555")
        self.label_file.pack(fill="x", **padding)

        # Detected format / target format row
        frame_middle = tk.Frame(self)
        frame_middle.pack(fill="x", **padding)

        tk.Label(frame_middle, text="Detected type:").grid(row=0, column=0, sticky="w")
        self.label_detected = tk.Label(frame_middle, text="-")
        self.label_detected.grid(row=0, column=1, sticky="w", padx=(4, 20))

        tk.Label(frame_middle, text="Convert to:").grid(row=0, column=2, sticky="e")

        self.target_format = tk.StringVar(value="mp3")
        self.combo_format = ttk.Combobox(
            frame_middle,
            textvariable=self.target_format,
            values=SUPPORTED_OUTPUT_FORMATS,
            state="readonly",
            width=8,
        )
        self.combo_format.grid(row=0, column=3, sticky="w", padx=(4, 0))

        # Convert button
        self.btn_convert = tk.Button(
            self, text="Convert", command=self.on_convert, state="disabled"
        )
        self.btn_convert.pack(pady=(16, 4))

        # Status label
        self.label_status = tk.Label(self, text="", fg="#007700")
        self.label_status.pack(fill="x", padx=12, pady=(0, 8))

    def setup_drag_and_drop_hint(self):
        # NOTE: Native drag-and-drop is more involved; this is just a visual hint.
        # You can still drag onto the window title and drop, which pops a "Open with" dialog,
        # but the most reliable UX is clicking the drop area or Browse.
        pass

    def browse_file(self):
        filetypes = [
            ("Audio files", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac"),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="Select an audio file",
            filetypes=filetypes,
        )
        if not path:
            return

        self.set_input_file(path)

    def set_input_file(self, path: str):
        self.input_path = path

        filename = os.path.basename(path)
        self.label_file.config(text=filename)

        ext = get_extension(path)
        if ext:
            self.label_detected.config(text=ext)
        else:
            self.label_detected.config(text="unknown")

        self.btn_convert.config(state="normal")
        self.label_status.config(text="")

    def on_convert(self):
        if not self.input_path:
            messagebox.showwarning("No file", "Please select an audio file first.")
            return

        src_ext = get_extension(self.input_path)
        target_ext = self.target_format.get().lower()

        if not target_ext:
            messagebox.showwarning("No format", "Please choose a target format.")
            return

        if src_ext == target_ext:
            if not messagebox.askyesno(
                "Same format",
                f"The source is already {src_ext}. Convert anyway?",
            ):
                return

        base, _ = os.path.splitext(self.input_path)
        output_path = f"{base}_converted.{target_ext}"

        try:
            self.label_status.config(text="Converting...")
            self.update_idletasks()

            convert_audio(self.input_path, output_path)

        except Exception as e:
            self.label_status.config(text="")
            messagebox.showerror("Error", f"Conversion failed:\n{e}")
            return

        self.label_status.config(
            text=f"Done! Saved as: {os.path.basename(output_path)}"
        )
        messagebox.showinfo(
            "Conversion complete",
            f"Converted to {target_ext}.\n\nSaved as:\n{output_path}",
        )


if __name__ == "__main__":
    app = AudioConverterApp()
    app.mainloop()
