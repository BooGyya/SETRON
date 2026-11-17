"""AI 분석에서 실제 저장한 보고서의 로컬 목록. 문서 내용이나 DB를 복제하지 않는다."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView

from setron.ui.components import button, label
from setron.ui.theme import SECONDARY


class ChatReportLibrary(QWidget):
    def __init__(self, *, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("SETRON", "AIReportLibrary")
        try:
            saved = json.loads(self.settings.value("files", "[]"))
            self.entries = [e for e in saved if isinstance(e, dict) and isinstance(e.get("path"), str)
                            and isinstance(e.get("created"), str)] if isinstance(saved, list) else []
        except (ValueError, TypeError):
            self.entries = []
        body = QVBoxLayout(self)
        body.addWidget(label("AI 분석 보고서", 20, medium=True))
        body.addWidget(label("이 PC에서 AI 분석으로 저장한 파일 목록입니다. 원본 파일을 이동·삭제하면 열 수 없습니다.", 13, color=SECONDARY, wrap=True))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["파일명", "저장 시각", "형식", "파일 상태"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        body.addWidget(self.table)
        row = QHBoxLayout()
        refresh, open_file, open_folder = button("새로고침"), button("파일 열기"), button("폴더 열기")
        refresh.clicked.connect(self.refresh)
        open_file.clicked.connect(lambda: self._open(False))
        open_folder.clicked.connect(lambda: self._open(True))
        for widget in (refresh, open_file, open_folder):
            row.addWidget(widget)
        row.addStretch(1)
        body.addLayout(row)
        self.message = label("", 12, color=SECONDARY, wrap=True)
        body.addWidget(self.message)
        self.refresh()

    def add(self, paths):
        for path in paths:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() not in (".pdf", ".docx"):
                continue
            self.entries = [e for e in self.entries if e["path"] != str(path)]
            self.entries.insert(0, {"path": str(path), "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self.settings.setValue("files", json.dumps(self.entries, ensure_ascii=False))
        self.settings.sync()
        self.refresh()
        if self.settings.status() != QSettings.Status.NoError:
            self.message.setText("파일은 저장됐지만 이 PC의 보고서 목록을 보관하지 못했습니다.")

    def refresh(self):
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            path = Path(entry["path"])
            for column, value in enumerate((path.name, entry["created"], path.suffix.removeprefix(".").upper(), "저장됨" if path.is_file() else "파일 없음")):
                item = QTableWidgetItem(value)
                item.setToolTip(str(path))
                self.table.setItem(row, column, item)
        self.message.setText("저장한 AI 보고서가 없습니다. AI 분석의 ‘보고서로 저장’을 사용해 주세요." if not self.entries else f"총 {len(self.entries)}개 · 이 PC의 저장 목록")

    def _open(self, folder):
        row = self.table.currentRow()
        if row < 0:
            self.message.setText("파일을 먼저 선택해 주세요.")
            return
        path = Path(self.entries[row]["path"])
        path = path.parent if folder else path
        if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.message.setText("파일 또는 폴더를 열지 못했습니다. 저장 위치를 확인해 주세요.")
