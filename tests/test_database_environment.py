"""설정 예시의 비밀번호 보존과 개발·테스트 DB 분리를 검증한다."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_PASSWORD = "dev_${SETRON_UNSET_REVIEW_VALUE}_$test#@:/ password"
TEST_PASSWORD = "test_${SETRON_UNSET_REVIEW_VALUE}_$test#@:/ password"


@pytest.fixture
def example_environment(tmp_path):
    """설정 예시에 특수문자 비밀번호를 넣고 외부 DB 설정의 영향을 제거한다.

    param
    - tmp_path: 테스트용 환경변수 파일을 만들 임시 디렉터리.

    return
    - 환경변수 파일 경로와 자식 프로세스에 전달할 실행 환경의 튜플.
    """
    content = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace_with_dev_password", DEV_PASSWORD)
    content = content.replace("replace_with_test_password", TEST_PASSWORD)
    env_file = tmp_path / "example.env"
    env_file.write_text(content, encoding="utf-8")
    # 테스트 파일보다 셸의 접속 설정이 우선하지 않도록 실행 환경을 분리한다.
    environ = {
        key: value for key, value in os.environ.items()
        if not key.startswith("SETRON_") and key not in {"UV_ENV_FILE", "UV_NO_ENV_FILE"}
    }
    return env_file, environ


def test_uv_preserves_literal_passwords_from_example(example_environment):
    """uv가 환경변수 파일의 달러 기호·공백 등 비밀번호 원문을 보존하는지 검증한다.

    uv가 설치되지 않은 환경에서는 건너뛴다.

    param
    - example_environment: 비밀번호 검증용 환경변수 파일과 분리된 실행 환경.
    """
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv가 설치되지 않았습니다.")
    env_file, environ = example_environment
    script = (
        "import json; from setron.infrastructure.database import DatabaseConfig; "
        "print(json.dumps([DatabaseConfig.from_env().password, "
        "DatabaseConfig.from_env(prefix='SETRON_TEST_DB_').password]))"
    )
    result = subprocess.run(
        [uv, "run", "--no-sync", "--env-file", env_file.as_posix(), sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environ,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert json.loads(result.stdout) == [DEV_PASSWORD, TEST_PASSWORD]


def test_compose_isolates_databases_and_preserves_passwords(example_environment):
    """Compose 설정에서 비밀번호가 보존되고 개발·테스트 DB가 분리되는지 검증한다.

    Docker CLI가 설치되지 않은 환경에서는 건너뛴다. 컨테이너를 실행하지 않고
    DB 이름·사용자·호스트 포트·볼륨과 로컬 주소 바인딩을 확인한다.

    param
    - example_environment: 비밀번호 검증용 환경변수 파일과 분리된 실행 환경.
    """
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI가 설치되지 않았습니다.")
    env_file, environ = example_environment
    result = subprocess.run(
        [
            docker, "compose", "--env-file", env_file.as_posix(),
            "--profile", "test", "config", "--format", "json",
        ],
        cwd=PROJECT_ROOT,
        env=environ,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    services = json.loads(result.stdout)["services"]
    dev, test = services["mysql-dev"], services["mysql-test"]
    # config 출력은 다시 읽을 수 있도록 $를 $$로 이스케이프한다.
    assert dev["environment"]["MYSQL_PASSWORD"].replace("$$", "$") == DEV_PASSWORD
    assert test["environment"]["MYSQL_PASSWORD"].replace("$$", "$") == TEST_PASSWORD
    assert dev["environment"]["MYSQL_DATABASE"] != test["environment"]["MYSQL_DATABASE"]
    assert dev["environment"]["MYSQL_USER"] != test["environment"]["MYSQL_USER"]
    assert dev["ports"][0]["published"] != test["ports"][0]["published"]
    assert dev["volumes"][0]["source"] != test["volumes"][0]["source"]
    for service in (dev, test):
        assert service["ports"][0]["host_ip"] == "127.0.0.1"
        assert service["ports"][0]["target"] == 3306
