"""기존 Docker MySQL에 시연 자료를 넣는 포터블 EXE·배치·SQL만 만든다.

    .venv/Scripts/python.exe packaging/build_docker_demo_db.py --dump <배포할 SETRON SQL>

DB와 Docker에는 접속하지 않는다. --dump는 53대 원천과 300대 시연 자료를
구분하기 위해 필수이며 Windows 배포 폴더와 원본 SQL은 변경하지 않는다.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "SETRON_Docker_DB"
NAME = "SETRON_DOCKER_DB_INIT"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True, help="실제 배포할 완전한 SETRON SQL")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source, output = args.dump.resolve(strict=True), args.output_dir.resolve()
    if output == (ROOT / "dist" / "SETRON").resolve():
        raise ValueError("Docker 배포 폴더는 Windows 앱 배포 폴더와 구분해 주세요.")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "database")]
    import init_demo_dump as shared
    shared._check_references(shared.read_dataset(source), shared.schema_statements())
    with tempfile.TemporaryDirectory(prefix="setron-docker-db-build-") as temporary:
        stage = Path(temporary).resolve()
        if stage.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("임시 빌드 경로를 확인해 주세요.")
        bundled = stage / "dataset.dump"
        shutil.copyfile(source, bundled)
        separator = ";" if sys.platform == "win32" else ":"
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--name", NAME,
            "--distpath", str(stage / "dist"), "--workpath", str(stage / "work"), "--specpath", str(stage),
            "--paths", str(ROOT / "src"), "--paths", str(ROOT / "database"),
            "--add-data", f"{bundled}{separator}.",
            "--add-data", f"{ROOT / 'database' / 'schema_mysql5619.sql'}{separator}.",
            str(ROOT / "database" / "init_docker_demo_dump.py"),
        ], cwd=ROOT, check=True)
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stage / "dist" / f"{NAME}.exe", output / f"{NAME}.exe")
        # Git/editor settings may change line endings; CMD receives CRLF only.
        wrapper = (ROOT / "prepare_docker_demo_db.bat").read_bytes()
        wrapper = wrapper.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        (output / "prepare_docker_demo_db.bat").write_bytes(wrapper.replace(b"\n", b"\r\n"))
        if source != output / "SETRON_DB.sql":
            shutil.copyfile(source, output / "SETRON_DB.sql")
    print(output)


if __name__ == "__main__":
    main()
