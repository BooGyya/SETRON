FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gosu libaio1 libncurses5 libnuma1 libstdc++6 perl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system mysql \
    && useradd --system --gid mysql --home-dir /var/lib/mysql mysql

# Oracle 공식 배포 파일을 받은 뒤 게시된 체크섬과 비교한다.
RUN curl --fail --location --retry 3 \
        https://cdn.mysql.com/archives/mysql-5.6/mysql-5.6.19-linux-glibc2.5-x86_64.tar.gz \
        --output /tmp/mysql.tar.gz \
    && echo '5e0e455c5a02b661b10561ad60e86a19  /tmp/mysql.tar.gz' | md5sum --check - \
    && mkdir -p /usr/local/mysql \
    && tar -xzf /tmp/mysql.tar.gz --strip-components=1 -C /usr/local/mysql \
    && rm /tmp/mysql.tar.gz \
    && rm -rf /usr/local/mysql/mysql-test /usr/local/mysql/sql-bench \
    && /usr/local/mysql/bin/mysqld --version | grep '5.6.19'

ENV PATH="/usr/local/mysql/bin:${PATH}"
COPY entrypoint.sh /usr/local/bin/setron-mysql-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/setron-mysql-entrypoint \
    && chmod +x /usr/local/bin/setron-mysql-entrypoint

VOLUME /var/lib/mysql
EXPOSE 3306
ENTRYPOINT ["/usr/local/bin/setron-mysql-entrypoint"]
CMD ["mysqld"]
