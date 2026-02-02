import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# =============================
# CONFIG (EDIT THESE)
# =============================
UPSTREAM_REPO = "ronnuriel/promo-site"   # PR target (your main repo)
DEFAULT_BRANCH = "main"                 # usually "main"

# Make GUI apps (PyInstaller .app) see Homebrew paths too
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
os.environ["PATH"] = ":".join(EXTRA_PATHS + [os.environ.get("PATH", "")])

try:
    from PIL import Image
    PIL_OK = True
except Exception:
    PIL_OK = False


# =============================
# Utilities
# =============================
def find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    for _ in range(50):
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


def app_start_dir() -> Path:
    """
    When packaged with PyInstaller, sys.executable is inside:
    ...EventFolderMaker.app/Contents/MacOS/EventFolderMaker
    We'll climb up a few parents to reach repo/tools folder.
    """
    exe = Path(sys.executable).resolve()
    if exe.name.lower().startswith("python"):
        return Path.cwd()
    if len(exe.parents) > 3:
        return exe.parents[3]
    return Path.cwd()


def command_exists(name: str) -> bool:
    from shutil import which
    if which(name):
        return True
    candidates = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        f"/bin/{name}",
    ]
    return any(os.path.exists(p) and os.access(p, os.X_OK) for p in candidates)


def tool_path(name: str) -> str:
    from shutil import which
    p = which(name)
    if p:
        return p
    candidates = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        f"/bin/{name}",
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return name


def run_cmd(cmd: list[str], cwd: str, log_fn):
    log_fn(f"$ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if p.stdout.strip():
        log_fn(p.stdout.strip())
    if p.stderr.strip():
        log_fn(p.stderr.strip())
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def gh_authed(repo_cwd: str) -> bool:
    p = subprocess.run([tool_path("gh"), "auth", "status"], cwd=repo_cwd, text=True, capture_output=True)
    return p.returncode == 0


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "event"


def validate_date(date_str: str) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise ValueError("תאריך לא תקין. השתמש בפורמט YYYY-MM-DD (למשל 2026-03-07).")


def validate_time(time_str: str) -> str:
    s = time_str.strip()
    if not s:
        return "00:00"
    if not re.fullmatch(r"\d{2}:\d{2}", s):
        raise ValueError("שעה לא תקינה. השתמש בפורמט HH:MM (למשל 22:00).")
    hh, mm = map(int, s.split(":"))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("שעה לא תקינה (טווח).")
    return s


def default_events_root() -> str:
    root = find_repo_root(app_start_dir()) or find_repo_root(Path.cwd())
    if root:
        return str(root / "events")
    return os.path.join(os.path.expanduser("~"), "Desktop", "events")


def get_origin_url(repo: str) -> str:
    p = subprocess.run([tool_path("git"), "remote", "get-url", "origin"], cwd=repo, text=True, capture_output=True)
    return (p.stdout or "").strip()


def ensure_origin_is_fork(repo: str):
    """
    Safety: partner should push to their fork (origin), not to upstream repo.
    """
    origin = get_origin_url(repo)
    if "ronnuriel/promo-site" in origin:
        raise RuntimeError(
            "ה-origin מצביע על הריפו הראשי (ronnuriel/promo-site).\n"
            "כדי לעבוד נכון צריך לעבוד מתוך Fork (origin צריך להיות הפורק שלך)."
        )


def sync_to_main_local(repo: str, log_fn):
    """
    Always start from main locally (even if gh not logged in).
    """
    log_fn(f"Force checkout {DEFAULT_BRANCH} ...")
    try:
        run_cmd([tool_path("git"), "checkout", DEFAULT_BRANCH], cwd=repo, log_fn=log_fn)
    except Exception as e:
        log_fn(f"Warning: checkout failed: {e}")

    log_fn("Pull from origin (fork) ...")
    try:
        run_cmd([tool_path("git"), "pull", "--ff-only"], cwd=repo, log_fn=log_fn)
    except Exception as e:
        log_fn(f"Warning: pull failed: {e}")


def sync_fork_with_upstream(repo: str, log_fn):
    """
    Best-effort: align fork with upstream repo using gh.
    """
    log_fn(f"Sync fork from upstream: {UPSTREAM_REPO} ({DEFAULT_BRANCH}) ...")
    try:
        run_cmd([tool_path("gh"), "repo", "sync", "--repo", UPSTREAM_REPO, "-b", DEFAULT_BRANCH], cwd=repo, log_fn=log_fn)
    except Exception as e:
        log_fn(f"Warning: gh repo sync failed (continuing): {e}")


# =============================
# GUI App
# =============================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Event Folder Maker")
        self.geometry("620x920")
        self.minsize(620, 880)
        self.resizable(True, True)

        self.image_path = tk.StringVar(value="")
        self.auto_pr = tk.BooleanVar(value=True)

        self._build_ui()

        # Auto-run startup sync shortly after opening
        self.after(350, self.startup_sync)

    def _build_ui(self):
        header = tk.Label(
            self,
            text="הוספת אירוע (תיקייה + meta.json + cover.jpg)\nאופציונלי: Sync + Push + PR אוטומטי",
            font=("Arial", 13, "bold"),
            justify="center"
        )
        header.pack(pady=10)

        frm = tk.Frame(self)
        frm.pack(fill="x", padx=14, pady=4)

        self.title_var = self._row(frm, "שם האירוע*", "")
        self.date_var = self._row(frm, "תאריך* (YYYY-MM-DD)", datetime.now().strftime("%Y-%m-%d"))
        self.time_var = self._row(frm, "שעה* (HH:MM)", "22:00")
        self.loc_var = self._row(frm, "מיקום", "")
        self.ticket_var = self._row(frm, "לינק לכרטיסים*", "")
        self.coupon_var = self._row(frm, "קופון", "")
        self.desc_var = self._row(frm, "תיאור קצר", "", multiline=True)

        imgfrm = tk.Frame(self)
        imgfrm.pack(fill="x", padx=14, pady=4)
        tk.Label(imgfrm, text="תמונה/פלייר*").grid(row=0, column=0, sticky="w")
        tk.Entry(imgfrm, textvariable=self.image_path, width=56).grid(row=1, column=0, sticky="w", pady=4)
        tk.Button(imgfrm, text="בחר קובץ…", command=self.pick_image).grid(row=1, column=1, padx=8)

        outfrm = tk.Frame(self)
        outfrm.pack(fill="x", padx=14, pady=4)
        tk.Label(outfrm, text="איפה ליצור את התיקיות (ברירת מחדל: events בתוך הריפו)").grid(row=0, column=0, sticky="w")
        self.out_root = tk.StringVar(value=default_events_root())
        tk.Entry(outfrm, textvariable=self.out_root, width=56).grid(row=1, column=0, sticky="w", pady=4)
        tk.Button(outfrm, text="בחר…", command=self.pick_out_root).grid(row=1, column=1, padx=8)

        optfrm = tk.Frame(self)
        optfrm.pack(fill="x", padx=14, pady=6)
        tk.Checkbutton(
            optfrm,
            text="אחרי יצירה: Sync + Push + Create PR אוטומטי",
            variable=self.auto_pr
        ).pack(anchor="w")

        btnfrm = tk.Frame(self)
        btnfrm.pack(fill="x", padx=14, pady=8)

        tk.Button(
            btnfrm,
            text="✅ צור תיקיית אירוע",
            font=("Arial", 12, "bold"),
            command=self.create_event
        ).pack(fill="x", pady=6)

        tk.Button(
            btnfrm,
            text="🚀 Push + PR (אם כבר יש שינויים ב-events/)",
            command=self.push_pr_only
        ).pack(fill="x", pady=6)

        tk.Button(
            btnfrm,
            text="🔄 Sync עכשיו (להתחיל מ-main)",
            command=self.manual_sync
        ).pack(fill="x", pady=6)

        tk.Button(
            btnfrm,
            text="🔐 התחבר ל-GitHub (gh auth login)",
            command=self.gh_login
        ).pack(fill="x", pady=6)

        hint = tk.Label(
            self,
            text=(
                "הערות:\n"
                f"- PR תמיד נפתח אל: {UPSTREAM_REPO}:{DEFAULT_BRANCH}\n"
                "- Push+PR עובד רק בתוך Clone אמיתי (לא ZIP) + git + gh.\n"
                "- אם gh לא מחובר: לחץ על 'התחבר ל-GitHub'."
            ),
            fg="#666",
            justify="left"
        )
        hint.pack(padx=14, pady=6, anchor="w")

        if not PIL_OK:
            warn = tk.Label(self, text="Pillow לא מותקן — התמונה תועתק כמו שהיא ונשמרת בשם cover.jpg.", fg="#a00")
            warn.pack(pady=2)

        loglbl = tk.Label(self, text="Log", font=("Arial", 11, "bold"))
        loglbl.pack(padx=14, pady=(8, 4), anchor="w")

        self.log = tk.Text(self, height=18, width=100)
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _row(self, parent, label, default, multiline=False):
        frame = tk.Frame(parent)
        frame.pack(fill="x", pady=4)
        tk.Label(frame, text=label).pack(anchor="w")

        if multiline:
            txt = tk.Text(frame, height=4, width=74)
            txt.insert("1.0", default)
            txt.pack(fill="x")

            class TVar:
                def get(self_inner): return txt.get("1.0", "end").strip()
                def set(self_inner, v):
                    txt.delete("1.0", "end")
                    txt.insert("1.0", v)

            return TVar()
        else:
            var = tk.StringVar(value=default)
            tk.Entry(frame, textvariable=var, width=82).pack(fill="x")
            return var

    def log_line(self, s: str):
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.update_idletasks()

    def pick_image(self):
        p = filedialog.askopenfilename(
            title="בחר פלייר/תמונה",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.heic"), ("All files", "*.*")]
        )
        if p:
            self.image_path.set(p)

    def pick_out_root(self):
        d = filedialog.askdirectory(title="בחר תיקיית יעד (root)")
        if d:
            self.out_root.set(d)

    def _detect_repo(self) -> str | None:
        # Prefer events root, then cwd, then app location
        candidates = [
            Path(self.out_root.get()) if self.out_root.get().strip() else Path.cwd(),
            Path.cwd(),
            app_start_dir(),
        ]
        for c in candidates:
            rr = find_repo_root(c)
            if rr:
                return str(rr)
        return None

    # =============================
    # Startup / Manual Sync
    # =============================
    def startup_sync(self):
        self.log_line("---- Startup Sync ----")

        repo = self._detect_repo()
        if not repo:
            self.log_line("No .git found (not a clone). Skipping sync.")
            return

        if not command_exists("git"):
            self.log_line("git missing. Skipping sync.")
            return

        # Always start from main locally
        sync_to_main_local(repo, self.log_line)

        # If gh exists and is authed, also align fork with upstream
        if command_exists("gh") and gh_authed(repo):
            try:
                ensure_origin_is_fork(repo)
                sync_fork_with_upstream(repo, self.log_line)
                # after syncing fork, pull again to get the updated fork
                sync_to_main_local(repo, self.log_line)
            except Exception as e:
                self.log_line(f"Warning: upstream sync skipped/failed: {e}")
        else:
            self.log_line("gh missing/not authed -> skipping upstream sync (local main is ok).")

        self.log_line("Startup sync done ✅")

    def manual_sync(self):
        try:
            self.log_line("---- Manual Sync ----")
            repo = self._detect_repo()
            if not repo:
                raise RuntimeError("לא מצאתי .git. צריך Clone אמיתי של הריפו (לא ZIP).")
            if not command_exists("git"):
                raise RuntimeError("חסר git.")

            sync_to_main_local(repo, self.log_line)

            if command_exists("gh"):
                if not gh_authed(repo):
                    raise RuntimeError("gh לא מחובר. לחץ 'התחבר ל-GitHub'.")
                ensure_origin_is_fork(repo)
                sync_fork_with_upstream(repo, self.log_line)
                sync_to_main_local(repo, self.log_line)

            messagebox.showinfo("Sync", "הסנכרון בוצע ✅")
        except Exception as e:
            messagebox.showerror("שגיאה", str(e))
            self.log_line(f"ERROR: {e}")

    # =============================
    # GH login
    # =============================
    def gh_login(self):
        try:
            self.log_line("---- gh auth login ----")
            if not command_exists("gh"):
                raise RuntimeError("לא מצאתי gh. התקן GitHub CLI (gh) ואז נסה שוב.")

            ghp = tool_path("gh")  # e.g. /opt/homebrew/bin/gh
            script = f'''
tell application "Terminal"
    activate
    do script "{ghp} auth login"
end tell
'''
            subprocess.run(["osascript", "-e", script], check=False)
            messagebox.showinfo("התחברות", "פתחתי Terminal עם gh auth login.\nסיים את התהליך שם ואז חזור לאפליקציה.")
        except Exception as e:
            messagebox.showerror("שגיאה", str(e))
            self.log_line(f"ERROR: {e}")

    # =============================
    # Create event
    # =============================
    def create_event(self):
        try:
            self.log_line("---- Create Event ----")

            title = self.title_var.get().strip()
            if not title:
                raise ValueError("חסר שם אירוע.")
            date = validate_date(self.date_var.get().strip())
            t = validate_time(self.time_var.get().strip())

            ticket_url = self.ticket_var.get().strip()
            if not ticket_url:
                raise ValueError("חסר לינק לכרטיסים.")

            img = self.image_path.get().strip()
            if not img or not os.path.isfile(img):
                raise ValueError("חובה לבחור תמונה קיימת.")

            location = self.loc_var.get().strip()
            coupon = self.coupon_var.get().strip()
            description = self.desc_var.get() if hasattr(self.desc_var, "get") else ""

            folder = f"{date}-{slugify(title)}"
            out_root = self.out_root.get().strip() or default_events_root()
            out_dir = os.path.join(out_root, folder)
            os.makedirs(out_dir, exist_ok=True)

            meta = {
                "title": title,
                "date": date,
                "time": t,
                "location": location,
                "description": description,
                "ticket_url": ticket_url,
                "promoter_url": "",
                "coupon_code": coupon,
                "image": "cover.jpg"
            }

            with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            cover_path = os.path.join(out_dir, "cover.jpg")
            if PIL_OK:
                try:
                    im = Image.open(img)
                    im = im.convert("RGB")
                    im.save(cover_path, format="JPEG", quality=92, optimize=True)
                except Exception:
                    shutil.copyfile(img, cover_path)
            else:
                shutil.copyfile(img, cover_path)

            self.log_line(f"Created: {out_dir}")
            messagebox.showinfo("הצלחה ✅", f"נוצר אירוע:\n{out_dir}\n\nבתיקייה יש meta.json + cover.jpg")

            if self.auto_pr.get():
                pr_title = f"Add event: {title} ({date})"
                self.push_pr(repo_hint_path=out_dir, pr_title=pr_title)

            # reset
            self.image_path.set("")
            self.title_var.set("")
            self.ticket_var.set("")
            self.coupon_var.set("")
            self.loc_var.set("")
            self.time_var.set("22:00")
            try:
                self.desc_var.set("")
            except Exception:
                pass

        except Exception as e:
            messagebox.showerror("שגיאה", str(e))
            self.log_line(f"ERROR: {e}")

    # =============================
    # Push PR only
    # =============================
    def push_pr_only(self):
        try:
            self.push_pr(repo_hint_path=None, pr_title=None)
        except Exception as e:
            messagebox.showerror("שגיאה", str(e))
            self.log_line(f"ERROR: {e}")

    # =============================
    # Push + PR
    # =============================
    def push_pr(self, repo_hint_path: str | None, pr_title: str | None):
        self.log_line("---- Push + PR ----")
        self.log_line(f"gh={tool_path('gh')}")
        self.log_line(f"git={tool_path('git')}")

        if not command_exists("git"):
            raise RuntimeError("לא מצאתי git. התקן GitHub Desktop או Xcode Command Line Tools.")
        if not command_exists("gh"):
            raise RuntimeError("לא מצאתי gh. התקן GitHub CLI (gh).")

        start = Path(repo_hint_path) if repo_hint_path else Path(self.out_root.get())
        repo_root = find_repo_root(start) or find_repo_root(Path.cwd()) or find_repo_root(app_start_dir())
        if not repo_root:
            raise RuntimeError("לא מצאתי .git. כדי ש-Push+PR יעבוד צריך Clone אמיתי של הריפו (לא ZIP).")

        repo = str(repo_root)
        self.log_line(f"Repo: {repo}")

        if not gh_authed(repo):
            do = messagebox.askyesno("לא מחובר ל-GitHub", "gh לא מחובר.\nרוצה להתחבר עכשיו? (ייפתח Terminal)")
            if do:
                self.gh_login()
            raise RuntimeError("צריך להתחבר ל-gh ואז לנסות שוב.")

        # must be fork
        ensure_origin_is_fork(repo)

        # Always start from main + align
        sync_to_main_local(repo, self.log_line)
        sync_fork_with_upstream(repo, self.log_line)
        sync_to_main_local(repo, self.log_line)

        # changes?
        p = subprocess.run([tool_path("git"), "status", "--porcelain", "events/"], cwd=repo, text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("git status נכשל.")
        if not p.stdout.strip():
            messagebox.showinfo("אין שינויים", "לא נמצאו שינויים בתוך events/.")
            self.log_line("No changes in events/.")
            return

        ts = time.strftime("%Y%m%d-%H%M%S")
        branch = f"partner/events-{ts}"

        run_cmd([tool_path("git"), "checkout", "-b", branch], cwd=repo, log_fn=self.log_line)
        run_cmd([tool_path("git"), "add", "events/"], cwd=repo, log_fn=self.log_line)

        title = pr_title or f"Update events ({ts})"
        run_cmd([tool_path("git"), "commit", "-m", title], cwd=repo, log_fn=self.log_line)
        run_cmd([tool_path("git"), "push", "-u", "origin", branch], cwd=repo, log_fn=self.log_line)

        # fork owner
        p_owner = subprocess.run(
            [tool_path("gh"), "repo", "view", "--json", "owner", "-q", ".owner.login"],
            cwd=repo, text=True, capture_output=True
        )
        if p_owner.returncode != 0 or not p_owner.stdout.strip():
            self.log_line(p_owner.stdout.strip())
            self.log_line(p_owner.stderr.strip())
            raise RuntimeError("לא הצלחתי לזהות owner של הפורק (gh repo view).")
        fork_owner = p_owner.stdout.strip()

        head = f"{fork_owner}:{branch}"

        pr_cmd = [
            tool_path("gh"), "pr", "create",
            "--repo", UPSTREAM_REPO,
            "--base", DEFAULT_BRANCH,
            "--head", head,
            "--title", title,
            "--body", "Auto PR from EventFolderMaker."
        ]

        p3 = subprocess.run(pr_cmd, cwd=repo, text=True, capture_output=True)
        if p3.returncode != 0:
            self.log_line(p3.stdout.strip())
            self.log_line(p3.stderr.strip())
            raise RuntimeError("לא הצלחתי ליצור PR אוטומטי. פתח PR ידנית מה-branch.")

        pr_url = p3.stdout.strip()
        self.log_line(f"PR: {pr_url}")

        # Always go back to main after success
        try:
            run_cmd([tool_path("git"), "checkout", DEFAULT_BRANCH], cwd=repo, log_fn=self.log_line)
            run_cmd([tool_path("git"), "pull", "--ff-only"], cwd=repo, log_fn=self.log_line)
            self.log_line(f"Back on {DEFAULT_BRANCH} ✅")
        except Exception as e:
            self.log_line(f"Warning: failed to return to {DEFAULT_BRANCH}: {e}")

        try:
            subprocess.run(["open", pr_url], cwd=repo)
        except Exception:
            pass

        messagebox.showinfo("PR נוצר ✅", f"פתחתי Pull Request:\n{pr_url}")


if __name__ == "__main__":
    app = App()
    app.mainloop()