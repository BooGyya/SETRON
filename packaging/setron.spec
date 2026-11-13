# PyInstaller 설정: Windows 실행 파일 폴더(dist/SETRON)를 만든다 (Jira S15P31B106-77).
# 빌드: packaging/build_windows.ps1

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

spec_dir = Path(SPECPATH).resolve()
database_dir = spec_dir.parent / "database"
gui_script = spec_dir / "run_setron.py"
init_script = database_dir / "init_local_db.py"

analysis = Analysis(
    [str(gui_script), str(init_script)],
    pathex=[str(spec_dir.parent / "src")],
    datas=[
        *collect_data_files("setron"),  # ui/assets의 아이콘·폰트
        (str(database_dir / "schema_mysql5619.sql"), "database"),
        (str(database_dir / "seed.sql"), "database"),
    ],
    # DB 드라이버와 최초 사용 시 불러오는 AI·Excel SDK도 배포 폴더에 포함한다.
    hiddenimports=["pymysql", "openai", "xlwings", *collect_submodules("sqlalchemy.dialects.mysql")],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
# 공통 런타임 훅은 유지하고 각 EXE에는 자기 시작 스크립트만 넣는다.
scripts = {Path(script[1]).resolve(): script for script in analysis.scripts}
runtime_hooks = [script for path, script in scripts.items() if path not in {gui_script, init_script}]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    [*runtime_hooks, scripts[gui_script]],
    [],
    exclude_binaries=True,
    name="SETRON",
    console=False,
    icon=str(spec_dir.parent / "src" / "setron" / "ui" / "assets" / "setron-window-icon.ico"),
)
db_init = EXE(
    pyz,
    [*runtime_hooks, scripts[init_script]],
    [],
    exclude_binaries=True,
    name="SETRON_DB_INIT",
    console=True,
)
coll = COLLECT(exe, db_init, analysis.binaries, analysis.datas, name="SETRON")
