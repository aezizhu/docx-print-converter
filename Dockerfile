# syntax=docker/dockerfile:1
FROM maven:3.9.9-eclipse-temurin-17 AS java-build
WORKDIR /build
COPY java/pom.xml ./pom.xml
COPY java/src ./src
RUN mvn -B -q package
RUN jlink --module-path "$JAVA_HOME/jmods" \
      --add-modules java.se,jdk.charsets,jdk.crypto.ec,jdk.localedata,jdk.unsupported,jdk.xml.dom,jdk.zipfs \
      --strip-debug --no-header-files --no-man-pages --compress=2 --output /opt/jre

FROM debian:bookworm-slim AS runtime
ARG ONLYOFFICE_VERSION=10.0.0
ARG ONLYOFFICE_SHA256=c841776e56f4987d38516f5718d7b87b89263436178a03a4bee29fd748a11c20
ARG FANDOL_SHA256=9278f01b417ded5766d98c3937192a1a6a2c73a5e94a3493fdfc932b2a55005a
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN test "$(dpkg --print-architecture)" = amd64 \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip python3 poppler-utils fontconfig \
      fonts-dejavu-core fonts-liberation2 fonts-noto-cjk fonts-droid-fallback \
      fonts-arphic-uming fonts-wqy-zenhei fonts-crosextra-carlito \
      libstdc++6 libgcc-s1 libx11-6 libxext6 libxrender1 \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fL --retry 3 "https://github.com/ONLYOFFICE/DocumentServer/releases/download/v${ONLYOFFICE_VERSION}/onlyoffice-documentserver_amd64.deb" -o /tmp/onlyoffice.deb \
 && echo "${ONLYOFFICE_SHA256}  /tmp/onlyoffice.deb" | sha256sum -c - \
 && dpkg-deb -x /tmp/onlyoffice.deb / \
 && rm /tmp/onlyoffice.deb
RUN curl -fL --retry 3 https://mirrors.ctan.org/fonts/fandol.zip -o /tmp/fandol.zip \
 && echo "${FANDOL_SHA256}  /tmp/fandol.zip" | sha256sum -c - \
 && unzip -q /tmp/fandol.zip -d /tmp/fandol \
 && mkdir -p /usr/share/fonts/opentype/fandol /usr/share/doc/fandol \
 && cp /tmp/fandol/fandol/FandolSong-*.otf /tmp/fandol/fandol/FandolHei-*.otf /usr/share/fonts/opentype/fandol/ \
 && cp /tmp/fandol/fandol/COPYING /tmp/fandol/fandol/README /usr/share/doc/fandol/ \
 && rm -rf /tmp/fandol /tmp/fandol.zip \
 && fc-cache -f
COPY scripts/prepare_onlyoffice_fonts.py /opt/docx-print-converter/scripts/prepare_onlyoffice_fonts.py
RUN cd /var/www/onlyoffice/documentserver/server/FileConverter/bin \
 && LD_LIBRARY_PATH="$PWD" ./x2t -create-allfonts "$PWD" /var/www/onlyoffice/documentserver/core-fonts \
 && python3 /opt/docx-print-converter/scripts/prepare_onlyoffice_fonts.py "$PWD" \
 && chmod -R a+rX /var/www/onlyoffice/documentserver
COPY --from=java-build /build/target/docx-print-converter.jar /opt/docx-print-converter/java/docx-print-converter.jar
COPY --from=java-build /opt/jre /opt/jre
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.md /opt/docx-print-converter/
COPY src /opt/docx-print-converter/src
RUN printf '#!/bin/sh\nexec python3 -m docx_print_converter.cli "$@"\n' > /usr/local/bin/docx-print-convert \
 && chmod 755 /usr/local/bin/docx-print-convert \
 && useradd --create-home --uid 10001 converter \
 && mkdir -p /work && chown converter:converter /work
ENV JAVA_HOME=/opt/jre PATH="/opt/jre/bin:$PATH" PYTHONPATH=/opt/docx-print-converter/src
USER converter
WORKDIR /work
ENTRYPOINT ["docx-print-convert"]
