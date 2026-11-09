# pretty_gui.py

import os
import customtkinter as ctk
from tkinter import filedialog, messagebox

from converter_core import convert_audio, get_extension
from analysis_core import read_metadata, detect_bpm_and_key

SUPPORTED_OUTPUT_FORMATS = ["wav", "mp3", "m4a", "flac", "ogg", "aac"]


class PrettyAudioConverter(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")       # "light" or "dark"
        ctk.set_default_color_theme("blue")  # "blue", "dark-blue", "green"

        self.title("Audio Converter")
        self.geometry("640x420")
        self.resizable(False, False)

        self.input_path = None
        self.metadata = None
        self.analysis = None

        self.build_ui()

    def build_ui(self):
        # Top title
        title_label = ctk.CTkLabel(
            self,
            text="Audio Converter & Analyzer",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_label.pack(pady=(12, 4))

        subtitle_label = ctk.CTkLabel(
            self,
            text="Drop in an audio file, see its details, choose a format, and convert.",
            font=ctk.CTkFont(size=13),
        )
        subtitle_label.pack(pady=(0, 10))

        # Main frame
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=16, pady=8)

        # Left: drop area + controls
        left_frame = ctk.CTkFrame(main_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        self.drop_area = ctk.CTkButton(
            left_frame,
            text="Drop file here\n(or click to browse)",
            height=140,
            corner_radius=12,
            border_width=2,
            border_color="#555555",
            fg_color="#1c1c1c",
            hover_color="#2a2a2a",
            command=self.browse_file,
        )
        self.drop_area.pack(fill="x", pady=(4, 8))

        # NOTE: Real native drag-and-drop requires extra packages (tkdnd or customtkinter extensions).
        # For now, this is a clear visual "drop box" that also works on click.

        # Detected + target format
        formats_frame = ctk.CTkFrame(left_frame)
        formats_frame.pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(formats_frame, text="Detected type:").grid(
            row=0, column=0, sticky="w", padx=(4, 4), pady=4
        )
        self.label_detected = ctk.CTkLabel(formats_frame, text="-")
        self.label_detected.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=4)

        ctk.CTkLabel(formats_frame, text="Convert to:").grid(
            row=0, column=2, sticky="e", padx=(4, 4), pady=4
        )

        self.target_format_var = ctk.StringVar(value="mp3")
        self.combo_format = ctk.CTkOptionMenu(
            formats_frame,
            variable=self.target_format_var,
            values=SUPPORTED_OUTPUT_FORMATS,
            width=90,
        )
        self.combo_format.grid(row=0, column=3, sticky="w", padx=(0, 4), pady=4)

        # Convert button + status
        self.btn_convert = ctk.CTkButton(
            left_frame,
            text="Convert",
            state="disabled",
            command=self.on_convert,
        )
        self.btn_convert.pack(pady=(8, 4))

        self.label_status = ctk.CTkLabel(left_frame, text="", text_color="#66ff66")
        self.label_status.pack(pady=(2, 4))

        # Right: metadata / analysis panel
        right_frame = ctk.CTkFrame(main_frame)
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        meta_title = ctk.CTkLabel(
            right_frame,
            text="File Details",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        meta_title.pack(anchor="w", padx=8, pady=(6, 4))

        self.meta_text = ctk.CTkTextbox(
            right_frame,
            width=260,
            height=260,
            wrap="word",
            activate_scrollbars=True,
        )
        self.meta_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.meta_text.configure(state="disabled")

    # -------- File handling --------

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

        # Update button text
        self.drop_area.configure(text=f"{filename}\n(click to change)")

        # Read metadata + analysis
        self.metadata = read_metadata(path)
        self.analysis = detect_bpm_and_key(path)

        # Detected type
        ext = get_extension(path)
        self.label_detected.configure(text=ext if ext else "unknown")

        # Enable convert
        self.btn_convert.configure(state="normal")

        # Show metadata in panel
        self.show_metadata_panel()

        # Clear status
        self.label_status.configure(text="")

    def show_metadata_panel(self):
        self.meta_text.configure(state="normal")
        self.meta_text.delete("1.0", "end")

        if not self.metadata:
            self.meta_text.insert("end", "No metadata available.\n")
        else:
            m = self.metadata
            self.meta_text.insert("end", f"File: {m.get('filename')}\n")
            if m.get("duration_sec") is not None:
                minutes = int(m["duration_sec"] // 60)
                seconds = int(m["duration_sec"] % 60)
                self.meta_text.insert(
                    "end", f"Duration: {minutes}:{seconds:02d} min\n"
                )
            if m.get("samplerate") is not None:
                self.meta_text.insert("end", f"Sample rate: {m['samplerate']} Hz\n")
            if m.get("channels") is not None:
                self.meta_text.insert("end", f"Channels: {m['channels']}\n")

            # Tags
            if m.get("tags"):
                self.meta_text.insert("end", "\nTags:\n")
                for k, v in m["tags"].items():
                    self.meta_text.insert("end", f"  {k}: {v}\n")

        # Analysis
        if self.analysis:
            self.meta_text.insert("end", "\nAnalysis:\n")
            bpm = self.analysis.get("bpm")
            key = self.analysis.get("key")
            if bpm is not None:
                self.meta_text.insert("end", f"  Estimated BPM: {bpm}\n")
            if key is not None:
                self.meta_text.insert("end", f"  Estimated key: {key}\n")

        self.meta_text.configure(state="disabled")

    # -------- Conversion --------

    def on_convert(self):
        if not self.input_path:
            messagebox.showwarning("No file", "Please select an audio file first.")
            return

        src_ext = get_extension(self.input_path)
        target_ext = self.target_format_var.get().lower()

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
            self.label_status.configure(text="Converting…")
            self.update_idletasks()

            convert_audio(self.input_path, output_path)

        except Exception as e:
            self.label_status.configure(text="")
            messagebox.showerror("Error", f"Conversion failed:\n{e}")
            return

        self.label_status.configure(
            text=f"Done → {os.path.basename(output_path)}"
        )
        messagebox.showinfo(
            "Conversion complete",
            f"Converted to {target_ext}.\n\nSaved as:\n{output_path}",
        )


if __name__ == "__main__":
    app = PrettyAudioConverter()
    app.mainloop()
