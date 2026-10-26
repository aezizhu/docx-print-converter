# Third-party notices

The project wrapper is licensed under Apache License 2.0; see `LICENSE`. Third-party components retain their own licenses. Fonts are installed from distribution packages, not copied from Microsoft Office or Windows. No font binaries or third-party JARs are committed to this source repository.

## Java document conversion

- **docx4j 17.3.3**, including `docx4j-export-fo` and `docx4j-JAXB-ReferenceImpl`: Apache License 2.0. Copyright belongs to the docx4j contributors and their respective copyright holders. [Source and license](https://github.com/plutext/docx4j/tree/VERSION_17_3_3).
- **docx4j FO renderer**, the Apache FOP 2.11-derived renderer selected transitively by docx4j 17.3.3: Apache License 2.0. Contains work of the Apache Software Foundation and docx4j contributors. [Apache FOP](https://xmlgraphics.apache.org/fop/) and [docx4j export module](https://github.com/plutext/docx4j/tree/VERSION_17_3_3/docx4j-export-fo).
- **SLF4J**, including `slf4j-simple`: MIT license. [License](https://www.slf4j.org/license.html).
- **JUnit Jupiter**: Eclipse Public License 2.0; used for development tests and excluded from the runtime dependency scope. [License](https://github.com/junit-team/junit5/blob/main/LICENSE.md).

The shaded Java build retains dependency resources and merges service-provider declarations and common `META-INF/LICENSE`/`NOTICE` files. This summary is not a replacement for each dependency's notices. Transitive dependencies may use additional licenses; inspect the Maven dependency tree and the notices bundled in a release artifact before redistribution.

## Runtime fonts

The Java backend uses the Fandol OTF fonts. The combined renderer environment also uses open fonts from distribution packages:

- **Fandol 0.3**: GPL version 3 with its font-embedding exception. The Java build downloads the unmodified official CTAN package and retains its `COPYING` and `README` files. [Official package](https://ctan.org/pkg/fandol).
- **AR PL UMing** (`fonts-arphic-uming`): Arphic Public License, with the package's additional notices. [Debian package](https://packages.debian.org/bookworm/fonts-arphic-uming).
- **WenQuanYi Zen Hei** (`fonts-wqy-zenhei`): GPL version 2 with the font-embedding exception; the package also includes the relevant M+ font license terms. [Debian package](https://packages.debian.org/bookworm/fonts-wqy-zenhei).
- **Droid fallback fonts** (`fonts-droid-fallback`): Apache License 2.0. [Debian package](https://packages.debian.org/bookworm/fonts-droid-fallback).
- **Liberation fonts** (`fonts-liberation`): SIL Open Font License 1.1 for the current font package; consult the installed package's copyright file for its exact version and notices. [Debian package](https://packages.debian.org/bookworm/fonts-liberation).
- **DejaVu fonts** (`fonts-dejavu-core`): Bitstream Vera font license and public-domain contributions, as described by the project and distribution package. [License](https://dejavu-fonts.github.io/License.html).

Full installed package notices remain under `/usr/share/doc/<package>/copyright` in the container. Font substitutions are not a guarantee of identical glyph shapes or pagination.

## Shape renderer

- **ONLYOFFICE Community DocumentServer 10.0.0**, including the `x2t` converter, renderer libraries and SDK scripts: GNU Affero General Public License version 3, together with the additional conditions and notices in its upstream license. [Source and license](https://github.com/ONLYOFFICE/DocumentServer/tree/v10.0.0), [core source](https://github.com/ONLYOFFICE/core), [SDK source](https://github.com/ONLYOFFICE/sdkjs).
- The wrapper invokes the converter as a separate executable. Its Apache License 2.0 does not relicense ONLYOFFICE or override the requirements for redistributing a combined image. Keep upstream copyright/license notices and provide the applicable corresponding-source information when distributing that runtime.
- The generated font-index alias patch is project code. It changes alternate family-name metadata in generated indexes; it does not modify font glyphs or font files.

## Runtime and verification tools

- **OpenJDK 17**: GPL version 2 with the Classpath Exception, with component notices retained in the JRE `legal/` directory. [OpenJDK legal documentation](https://openjdk.org/legal/).
- **Poppler** (`pdfinfo`): GPL-licensed PDF tooling. Keep the distribution package's license and copyright notices. [Project](https://poppler.freedesktop.org/).

A source-only checkout does not contain renderer binaries, Java dependencies, fonts, or private documents. A built image does contain third-party runtime components and must retain their notices.
