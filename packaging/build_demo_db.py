"""별도 시연 DB 준비 EXE를 만든다. 저장소 루트에서 .venv Python으로 실행한다."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "docs" / "02_시스템_설계" / "데이터베이스"
NAME = "SETRON_DEMO_DB_SETUP"


def main() -> None:
    dump = (OUTPUT / "SETRON_시연_DB_초기화.sql").resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="setron-demo-db-build-") as staging:
        stage = Path(staging).resolve()
        if stage.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("임시 빌드 경로를 확인해 주세요.")
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--name", NAME,
            "--distpath", str(stage / "dist"), "--workpath", str(stage / "work"), "--specpath", str(stage),
            "--paths", str(ROOT / "src"), "--add-data", f"{dump}{';' if sys.platform == 'win32' else ':'}.",
            str(ROOT / "database" / "init_demo_dump.py"),
        ], cwd=ROOT, check=True)
        output = OUTPUT / "SETRON_시연_DB_준비.exe"
        shutil.copyfile(stage / "dist" / f"{NAME}.exe", output)
        print(output)


if __name__ == "__main__":
    main()
