# Standalone Java backend reference image. The project router has its own image.
FROM maven:3.9.9-eclipse-temurin-17 AS java-build
WORKDIR /build
COPY java/pom.xml ./pom.xml
COPY java/src ./src
RUN mvn -B -ntp verify

FROM debian:bookworm-slim AS fonts
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -fL --retry 3 https://mirrors.ctan.org/fonts/fandol.zip -o /tmp/fandol.zip \
    && echo '9278f01b417ded5766d98c3937192a1a6a2c73a5e94a3493fdfc932b2a55005a  /tmp/fandol.zip' | sha256sum -c - \
    && unzip -q /tmp/fandol.zip -d /tmp \
    && mkdir /fonts /notices \
    && cp /tmp/fandol/FandolSong-*.otf /tmp/fandol/FandolHei-*.otf /fonts/ \
    && cp /tmp/fandol/COPYING /tmp/fandol/README /notices/

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig fonts-dejavu-core fonts-liberation fonts-droid-fallback \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /work \
    && chown 10001:10001 /work
COPY --from=fonts /fonts/ /usr/share/fonts/opentype/fandol/
COPY --from=fonts /notices/ /usr/share/doc/fandol/
RUN fc-cache -f
COPY --from=java-build /build/target/docx-print-converter.jar /app/converter.jar
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
WORKDIR /work
USER 10001:10001
ENTRYPOINT ["java", "-Djava.awt.headless=true", "-Duser.timezone=Asia/Singapore", "-Xmx1g", "-jar", "/app/converter.jar"]
