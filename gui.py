import queue
import re
import sqlite3
import sys

from PyQt5.QtCore import QDateTime, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication, QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenuBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from main import (
    download_tiktok_profile,
    download_tiktok_video,
    get_downloads_folder,
)

DB_FILE = "profiles.db"


class DownloadWorker(QThread):
    finished = pyqtSignal(str, str, str)

    def __init__(self, profile_name, output_folder):
        super().__init__()
        self.profile_name = profile_name
        self.output_folder = output_folder

    def run(self):
        url = f"https://www.tiktok.com/@{self.profile_name}"
        download_tiktok_profile(url)
        last_updated = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.finished.emit(self.profile_name, last_updated, "Updated")


class VideoDownloadWorker(QThread):
    finished = pyqtSignal(str, str, str)

    def __init__(self, url, output_folder):
        super().__init__()
        self.url = url
        self.output_folder = output_folder

    def run(self):
        metadata = download_tiktok_video(self.url, self.output_folder, [])
        if metadata:
            last_updated = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
            self.finished.emit(metadata["uploader"], last_updated, "Downloaded")


class TikTokDownloaderGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.active_threads = []
        self.selected_interval = 0  # Initialize selected interval
        self.auto_update_timer = None  # Initialize timer
        self.refresh_table_timer = None  # Global refresher timer
        self.auto_update_actions = []  # Store menu actions

        self.setWindowTitle("TikTok Video Downloader")
        self.setWindowIcon(QIcon("icon.png"))

        # Get screen size
        screen = QGuiApplication.primaryScreen().geometry()
        width = int(screen.width() * 0.4)
        height = int(screen.height() * 0.4)

        # Set window size
        self.setGeometry(
            (screen.width() - width) // 2,  # X position (centered)
            (screen.height() - height) // 2,  # Y position (centered)
            width,
            height,
        )

        self.init_db()

        self.update_buttons = {}

        self.init_ui()

        self.update_queue = queue.Queue()
        self.current_worker = None

        # Load the saved auto-update interval from the database
        self.load_auto_update_interval()

        # Initialize and start the table refresher
        self.start_table_refresher()

        self.is_initialized = True

    def load_auto_update_interval(self):
        """Load the auto-update interval from the database and set it globally."""
        self.cursor.execute("SELECT auto_update_interval FROM profiles WHERE profile_name = 'global'")
        result = self.cursor.fetchone()
        if result:
            self.selected_interval = result[0]
        self.set_auto_update(self.selected_interval)  # Apply the saved interval globally

    def start_table_refresher(self):
        """Start a timer to periodically refresh the table's 'Last Update' column."""
        self.refresh_table_timer = QTimer(self)
        self.refresh_table_timer.timeout.connect(self.refresh_table)

        # Refresh timer in miliseconds
        self.refresh_table_timer.start(30000)

    def refresh_table(self):
        """Refresh the 'Last Update' column in the table by fetching the latest data."""
        self.cursor.execute("SELECT profile_name, last_updated FROM profiles")
        profiles = self.cursor.fetchall()
        for profile_name, last_updated in profiles:
            row = self.find_row_by_profile_name(profile_name)
            if row != -1:
                self.profile_table.setItem(row, 1, QTableWidgetItem(self.relative_time(last_updated)))

    def init_db(self):
        self.conn = sqlite3.connect(DB_FILE)
        self.cursor = self.conn.cursor()

        self.check_and_migrate_db()

        self.conn = sqlite3.connect(DB_FILE)
        self.cursor = self.conn.cursor()
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                status TEXT NOT NULL,
                auto_update_interval INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def check_and_migrate_db(self):
        """Check if the 'auto_update_interval' column exists and add it if necessary."""
        # Query to get the current columns in the 'profiles' table
        self.cursor.execute("PRAGMA table_info(profiles)")
        columns = [column[1] for column in self.cursor.fetchall()]

        # If the 'auto_update_interval' column does not exist, add it
        if "auto_update_interval" not in columns:
            print("Column 'auto_update_interval' does not exist, adding it...")
            self.cursor.execute("ALTER TABLE profiles ADD COLUMN auto_update_interval INTEGER DEFAULT 0")
            self.conn.commit()

    def init_ui(self):
        layout = QVBoxLayout()

        # Create a menu bar
        menu_bar = QMenuBar(self)

        # Create hamburger menu
        menu_button = menu_bar.addMenu("☰")

        # Create actions
        settings_action = QAction("Change Output folder", self)
        settings_action.triggered.connect(self.open_settings)

        update_all_action = QAction("Update All", self)
        update_all_action.triggered.connect(self.update_all_profiles)

        # Auto update menu setup
        auto_update_menu = menu_button.addMenu("Auto Update")
        self.interval_labels = {
            0: "Off",
            30000: "Every 30 seconds",
            1800000: "Every 30 minutes",
            3600000: "Every 1 hour",
            7200000: "Every 2 hours",
            21600000: "Every 6 hours",
            43200000: "Every 12 hours",
            86400000: "Every 24 hours",
        }
        self.auto_update_actions = []

        for interval, label in self.interval_labels.items():
            action = QAction(label, self, checkable=True)
            action.setData(interval)
            action.triggered.connect(lambda checked, i=interval: self.set_auto_update(i))
            auto_update_menu.addAction(action)
            self.auto_update_actions.append(action)

        # Initialize the selection (if any), delayed until after full initialization
        QTimer.singleShot(0, self._initialize_auto_update)

        close_action = QAction("Close", self)
        close_action.triggered.connect(self.close)

        # Add actions to the menu
        menu_button.addAction(settings_action)
        menu_button.addAction(update_all_action)
        menu_button.addAction(auto_update_menu.menuAction())
        menu_button.addAction(close_action)

        layout.setMenuBar(menu_bar)  # Attach menu bar to layout

        self.url_input_label = QLabel("Enter TikTok URL or @profile_name:", self)
        layout.addWidget(self.url_input_label)

        url_layout = QHBoxLayout()
        self.url_input = QLineEdit(self)
        self.url_input.textChanged.connect(self.validate_url_input)  # Connect input validation
        self.download_button = QPushButton("Download Video", self)
        self.download_button.setEnabled(False)  # Initially disabled
        self.download_button.clicked.connect(self.download_video)

        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.download_button)
        layout.addLayout(url_layout)

        self.output_folder = get_downloads_folder()

        self.profile_table = QTableWidget(self)
        self.profile_table.setColumnCount(4)
        self.profile_table.setHorizontalHeaderLabels(["Profile Name", "Last Update", "Status", ""])
        self.profile_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.profile_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.load_profiles()
        layout.addWidget(self.profile_table)

        # make column with index 3 fit content
        self.profile_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        self.setLayout(layout)

    def _initialize_auto_update(self):
        """Initialize auto-update settings after everything is fully initialized."""
        self.set_auto_update(self.selected_interval or 0)

    def set_auto_update(self, interval):
        """Set or restart the global auto-update timer."""
        self.selected_interval = interval

        # Save the selected interval globally in the database
        self.cursor.execute(
            """
            UPDATE profiles SET auto_update_interval = ? WHERE profile_name = 'global'
        """,
            (interval,),
        )
        self.conn.commit()

        # Stop any existing auto-update timers if they exist
        if self.auto_update_timer:
            self.auto_update_timer.stop()

        # Set the global auto-update timer
        if interval > 0:
            self.auto_update_timer = QTimer(self)
            self.auto_update_timer.timeout.connect(self.update_all_profiles)
            self.auto_update_timer.start(interval)

        # Update the menu actions to reflect the selected option
        for action in self.auto_update_actions:
            action.setChecked(action.data() == interval)

    def validate_url_input(self):
        text = self.url_input.text().strip()

        # Regex for valid TikTok URLs (profile, video, and short links)
        tiktok_url_pattern = re.compile(
            r"^(https?:\/\/)?(www\.)?tiktok\.com\/(@[\w.-]+(\/video\/\d+)?|[\w/-]+)(\?.*)?$"
            r"|^(https?:\/\/)?vm\.tiktok\.com\/[\w/-]+\/?$"
        )

        # Regex for TikTok profile usernames (e.g., @username)
        tiktok_profile_pattern = re.compile(r"^@[\w.-]+$")

        if tiktok_url_pattern.match(text) or tiktok_profile_pattern.match(text):
            self.download_button.setEnabled(True)
        else:
            self.download_button.setEnabled(False)

    def open_settings(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder  # Update stored output folder

    def load_profiles(self):
        self.cursor.execute("SELECT profile_name, last_updated, status FROM profiles")
        profiles = self.cursor.fetchall()
        self.profile_table.setRowCount(len(profiles))
        for i, profile in enumerate(profiles):
            profile_name, last_updated, status = profile
            self.profile_table.setItem(i, 0, QTableWidgetItem(profile_name))
            self.profile_table.setItem(i, 1, QTableWidgetItem(self.relative_time(last_updated)))
            self.profile_table.setItem(i, 2, QTableWidgetItem(status))

            update_button = QPushButton("Update", self)
            update_button.setFixedSize(update_button.sizeHint())
            update_button.clicked.connect(lambda _, name=profile_name, idx=i+1: self.update_profile(name, idx))
            self.profile_table.setCellWidget(i, 3, update_button)

            self.update_buttons[profile_name] = update_button  # Store button reference

    def relative_time(self, timestamp):
        last_updated = QDateTime.fromString(timestamp, "yyyy-MM-dd HH:mm:ss")
        if not last_updated.isValid():
            return timestamp
        now = QDateTime.currentDateTime()
        secs_diff = last_updated.secsTo(now)
        if secs_diff < 60:
            if secs_diff == 0:
                return "Just now"
            return f"{secs_diff} second{'s' if secs_diff > 1 else ''} ago"
        elif secs_diff < 3600:
            return f"{secs_diff // 60} minute{'s' if secs_diff // 60 > 1 else ''} ago"
        elif secs_diff < 86400:
            return f"{secs_diff // 3600} hour{'s' if secs_diff // 3600 > 1 else ''} ago"
        else:
            return last_updated.toString("yyyy-MM-dd")

    def get_last_updated(self, profile_name):
        """Retrieve the last updated timestamp for a profile."""
        self.cursor.execute("SELECT last_updated FROM profiles WHERE profile_name = ?", (profile_name,))
        result = self.cursor.fetchone()
        if result:
            return QDateTime.fromString(result[0], "yyyy-MM-dd HH:mm:ss")
        return QDateTime()

    def download_video(self):
        url = self.url_input.text()
        output_folder = self.output_folder
        if not url or not output_folder:
            return
        self.download_button.setEnabled(False)
        worker = VideoDownloadWorker(url, output_folder)
        worker.finished.connect(self.on_video_download_finished)
        worker.finished.connect(self.update_profile_table)
        worker.start()
        self.active_threads.append(worker)

    def update_profile(self, profile_name, index):
        if profile_name in self.update_buttons:
            self.update_buttons[profile_name].setDisabled(True)
            self.update_buttons[profile_name].setText(f"In queue ({index})")
        self.update_queue.put(profile_name)
        self.process_queue()

    def process_queue(self):
        if self.current_worker is None and not self.update_queue.empty():
            profile_name = self.update_queue.get()
            self.update_status(profile_name, "Being Updated...")
            self.current_worker = DownloadWorker(profile_name, self.output_folder)
            self.current_worker.finished.connect(self.on_update_finished)
            self.current_worker.start()

    def on_update_finished(self, profile_name, last_updated, status):
        self.update_profile_table(profile_name, last_updated, status)

        # Re-enable the button after the update finishes
        if profile_name in self.update_buttons:
            self.update_buttons[profile_name].setDisabled(False)
            self.update_buttons[profile_name].setText("Update")

        self.current_worker = None
        self.process_queue()

    def on_video_download_finished(self, profile_name, last_updated, status):
        self.update_profile_table(profile_name, last_updated, status)
        self.cleanup_threads()
        self.download_button.setEnabled(True)
        self.url_input.clear()

    def cleanup_threads(self):
        for thread in self.active_threads:
            thread.quit()
            thread.wait()
        self.active_threads = []

    def closeEvent(self, event):
        """Ensure all threads and timers are stopped when closing."""
        if self.auto_update_timer:
            self.auto_update_timer.stop()
        for thread in self.active_threads:
            thread.wait()
        event.accept()

    def update_all_profiles(self):
        self.cursor.execute("SELECT profile_name FROM profiles")
        profiles = self.cursor.fetchall()
        for profile in profiles:
            self.update_profile(profile[0])

    def update_profile_table(self, profile_name, last_updated, status):
        self.cursor.execute(
            """UPDATE profiles SET last_updated = ?, status = ? WHERE profile_name = ?""",
            (last_updated, status, profile_name),
        )
        self.conn.commit()

        # Find the row of the updated profile
        row = self.find_row_by_profile_name(profile_name)
        if row != -1:
            self.profile_table.setItem(row, 1, QTableWidgetItem(self.relative_time(last_updated)))
            self.profile_table.setItem(row, 2, QTableWidgetItem(status))

    def update_status(self, profile_name, status):
        row = self.find_row_by_profile_name(profile_name)
        if row != -1:
            self.profile_table.setItem(row, 2, QTableWidgetItem(status))

    def find_row_by_profile_name(self, profile_name):
        for row in range(self.profile_table.rowCount()):
            if self.profile_table.item(row, 0).text() == profile_name:
                return row
        return -1


def main():
    print("Starting the application...")

    try:
        app = QApplication(sys.argv)
        gui = TikTokDownloaderGUI()
        gui.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
