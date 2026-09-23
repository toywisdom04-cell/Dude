import os
import queue
import threading

from core.config import get_config


class ChatWindow:
    def __init__(self, to_agent, from_agent):
        self.to_agent = to_agent
        self.from_agent = from_agent
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title("DUDE")
        self.root.geometry("420x520")
        self.root.configure(bg="#101418")
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

        header = tk.Label(self.root, text="D U D E", fg="#7fd7ff", bg="#101418",
                          font=("Segoe UI Semibold", 14))
        header.pack(pady=(10, 2))
        self.status = tk.Label(self.root, text="online", fg="#69db7c", bg="#101418",
                               font=("Segoe UI", 9))
        self.status.pack()

        self.log = tk.Text(self.root, bg="#161b22", fg="#e6edf3", wrap="word",
                           state="disabled", relief="flat",
                           font=("Segoe UI", 10), padx=12, pady=10)
        self.log.pack(fill="both", expand=True, padx=10, pady=8)

        row = tk.Frame(self.root, bg="#101418")
        row.pack(fill="x", padx=10, pady=(0, 10))
        self.entry = tk.Entry(row, bg="#1c2430", fg="#e6edf3", relief="flat",
                              font=("Segoe UI", 11), insertbackground="#e6edf3")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", self._send)
        btn = tk.Button(row, text="Send", command=self._send,
                        bg="#20536b", fg="white", relief="flat")
        btn.pack(side="left", padx=(6, 0))

        cam_btn = tk.Button(self.root, text="Camera", command=self._camera,
                            bg="#1c2430", fg="#aab8c5", relief="flat")
        cam_btn.pack(anchor="e", padx=10, pady=(0, 8))

        self._append("Dude is running. Type here or just talk.\n")
        self.root.after(200, self._poll)

    def _append(self, text, who=None):
        self.log.configure(state="normal")
        if who == "you":
            self.log.insert("end", f"You: {text}\n", "you")
            self.log.tag_config("you", foreground="#8ab4f8")
        elif who == "dude":
            self.log.insert("end", f"Dude: {text}\n", "dude")
            self.log.tag_config("dude", foreground="#7fd7ff")
        else:
            self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _send(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._append(text, "you")
        self.to_agent.put({"type": "chat", "text": text})

    def _camera(self):
        self.to_agent.put({"type": "command", "text": "open camera mirror"})

    def set_status(self, s, color="#69db7c"):
        self.status.configure(text=s, fg=color)

    def _poll(self):
        try:
            while True:
                msg = self.from_agent.get_nowait()
                kind = msg.get("type")
                if kind == "speech":
                    self._append(msg["text"], "dude")
                elif kind == "heard":
                    self._append(msg["text"], "you")
                elif kind == "status":
                    self.set_status(msg["text"], msg.get("color", "#69db7c"))
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(120, self._poll)

    def run(self):
        self.root.mainloop()

    def quit(self):
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass


class Tray:
    def __init__(self, on_show, on_quit):
        self.on_show = on_show
        self.on_quit_cb = on_quit
        self.icon = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _make_image(self):
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (64, 64), "#0d1117")
        d = ImageDraw.Draw(img)
        d.ellipse((14, 14, 50, 50), outline="#7fd7ff", width=4)
        d.ellipse((28, 28, 36, 36), fill="#7fd7ff")
        return img

    def _run(self):
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Show chat", lambda: self.on_show(), default=True),
                pystray.MenuItem("Quit DUDE", lambda: self.on_quit()),
            )
            self.icon = pystray.Icon("dude", self._make_image(), "DUDE", menu)
            self.icon.run_detached()
        except Exception as e:
            print(f"[ui] tray unavailable: {e}")


class CameraMirror:
    def __init__(self):
        self.thread = None

    def open_async(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)

        self.thread.start()

    def _run(self):
        try:
            import cv2

            cam = cv2.VideoCapture(0)
            if not cam.isOpened():
                return
            while True:
                ret, frame = cam.read()
                if not ret:
                    break
                cv2.imshow("DUDE Camera (q to close)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            cam.release()
            cv2.destroyAllWindows()
        except Exception as e:
            print(f"[ui] camera failed: {e}")
