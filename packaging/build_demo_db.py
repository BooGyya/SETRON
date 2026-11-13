"""지정한 SQL을 포함한 Windows DB 준비 EXE·배치·SQL을 dist/SETRON에 만든다.

    .venv/Scripts/python.exe packaging/build_demo_db.py --dump <배포할 SETRON SQL>

교육용 53대 원천과 Docker에서 내보낸 300대 자료를 구분하도록 --dump는 필수다.
DB에는 접속하지 않고 docs의 원본·생성용 SQL은 수정하지 않는다.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "SETRON"
NAME = "SETRON_DB_INIT"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True, help="실제 배포할 완전한 SETRON SQL 파일")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT, help="EXE·배치·외부 SQL의 배포 폴더")
    args = parser.parse_args()
    dump = args.dump.resolve(strict=True)
    output = args.output_dir.resolve()
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "database")]
    from init_demo_dump import read_dataset, schema_statements, _check_references
    _check_references(read_dataset(dump), schema_statements())
    with tempfile.TemporaryDirectory(prefix="setron-demo-db-build-") as staging:
        stage = Path(staging).resolve()
        if stage.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("임시 빌드 경로를 확인해 주세요.")
        bundled = stage / "dataset.dump"
        shutil.copyfile(dump, bundled)
        separator = ";" if sys.platform == "win32" else ":"
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--name", NAME,
            "--distpath", str(stage / "dist"), "--workpath", str(stage / "work"), "--specpath", str(stage),
            "--paths", str(ROOT / "src"), "--paths", str(ROOT / "database"),
            "--add-data", f"{bundled}{separator}.",
            "--add-data", f"{ROOT / 'database' / 'schema_mysql5619.sql'}{separator}.",
            str(ROOT / "database" / "init_demo_dump.py"),
        ], cwd=ROOT, check=True)
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage / "dist" / f"{NAME}.exe", output / f"{NAME}.exe")
        shutil.copyfile(ROOT / "prepare_demo_db.bat", output / "prepare_demo_db.bat")
        if dump != output / "SETRON_DB.sql":
            shutil.copyfile(dump, output / "SETRON_DB.sql")
        print(output)


if __name__ == "__main__":
    main()
