#!/bin/sh
set -eu

case "$1" in
    -*) set -- mysqld "$@" ;;
esac

if [ "$1" != mysqld ]; then
    exec "$@"
fi
shift

datadir=/var/lib/mysql
socket=/run/mysqld/mysqld.sock
mkdir -p "$datadir" /run/mysqld
chown -R mysql:mysql "$datadir" /run/mysqld

if [ ! -d "$datadir/mysql" ]; then
    : "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
    : "${MYSQL_USER:?MYSQL_USER is required}"
    : "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"
    : "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
    case "$MYSQL_DATABASE" in
        *[!a-zA-Z0-9_]*|'') echo 'Use letters, digits and underscores for MYSQL_DATABASE.' >&2; exit 1 ;;
        mysql|information_schema|performance_schema|sys) echo 'Use an application database name.' >&2; exit 1 ;;
    esac
    case "$MYSQL_USER" in
        *[!a-zA-Z0-9_]*|''|root) echo 'Use a non-root application user with letters, digits and underscores.' >&2; exit 1 ;;
    esac
    [ "${#MYSQL_DATABASE}" -le 64 ] && [ "${#MYSQL_USER}" -le 16 ] || {
        echo 'MySQL 5.6 allows database names up to 64 and user names up to 16 characters.' >&2
        exit 1
    }

    /usr/local/mysql/scripts/mysql_install_db --no-defaults \
        --basedir=/usr/local/mysql --datadir="$datadir" --user=mysql
    gosu mysql mysqld --no-defaults --basedir=/usr/local/mysql --datadir="$datadir" \
        --socket=/tmp/mysql-init.sock --pid-file=/tmp/mysql-init.pid \
        --skip-networking --lower-case-table-names=1 "$@" &
    init_pid=$!
    trap 'kill "$init_pid" 2>/dev/null || true; wait "$init_pid" 2>/dev/null || true' EXIT INT TERM
    attempts=0
    until mysqladmin --no-defaults --socket=/tmp/mysql-init.sock --user=root ping --silent >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        [ "$attempts" -lt 60 ] || { echo 'MySQL initialization timed out.' >&2; exit 1; }
        sleep 1
    done

    # 따옴표나 달러 기호가 포함된 비밀번호도 원문대로 처리하도록 16진수로 전달한다.
    root_hex=$(printf '%s' "$MYSQL_ROOT_PASSWORD" | od -An -v -tx1 | tr -d ' \n')
    user_hex=$(printf '%s' "$MYSQL_PASSWORD" | od -An -v -tx1 | tr -d ' \n')
    root_hash=$(mysql --no-defaults --socket=/tmp/mysql-init.sock --user=root --batch --skip-column-names \
        --execute="SELECT PASSWORD(CONVERT(0x${root_hex} USING utf8mb4))")
    user_hash=$(mysql --no-defaults --socket=/tmp/mysql-init.sock --user=root --batch --skip-column-names \
        --execute="SELECT PASSWORD(CONVERT(0x${user_hex} USING utf8mb4))")
    mysql --no-defaults --socket=/tmp/mysql-init.sock --user=root <<SQL
DELETE FROM mysql.user WHERE NOT (User = 'root' AND Host = 'localhost');
DROP DATABASE IF EXISTS test;
CREATE DATABASE \`$MYSQL_DATABASE\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER '$MYSQL_USER'@'%' IDENTIFIED BY PASSWORD '$user_hash';
GRANT ALL PRIVILEGES ON \`$MYSQL_DATABASE\`.* TO '$MYSQL_USER'@'%';
SET PASSWORD FOR 'root'@'localhost' = '$root_hash';
FLUSH PRIVILEGES;
SQL
    MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin --no-defaults --socket=/tmp/mysql-init.sock --user=root shutdown
    wait "$init_pid"
    trap - EXIT INT TERM
fi

exec gosu mysql mysqld --no-defaults --basedir=/usr/local/mysql --datadir="$datadir" \
    --socket="$socket" --pid-file=/run/mysqld/mysqld.pid \
    --bind-address=0.0.0.0 --lower-case-table-names=1 "$@"
