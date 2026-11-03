# PyInstaller 설정: Windows 실행 파일 폴더(dist/SETRON)를 만든다 (Jira S15P31B106-77).
# 빌드: packaging/build_windows.ps1

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

analysis = Analysis(
    ["run_setron.py"],
    pathex=["../src"],
    datas=collect_data_files("setron"),  # ui/assets의 아이콘·폰트
    # SQLAlchemy는 접속 주소(mysql+pymysql)로 드라이버를 찾으므로 자동 분석에 잡히지 않는다.
    hiddenimports=["pymysql", *collect_submodules("sqlalchemy.dialects.mysql")],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SETRON",
    console=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="SETRON")
