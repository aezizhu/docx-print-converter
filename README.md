# DOCX Print Converter

A local DOCX-to-PDF converter for print stations and NAS systems. It combines two open-source layout engines and selects between them using document structure:

- **docx4j 17.3.3** for documents without floating tables or anchored shapes.
- **ONLYOFFICE Community 10.0.0** for floating tables, legacy VML shapes, text frames, and anchored drawings.

The router never selects a renderer by filename, company name, document hash, or cached page count. Processing stays on your machine. It does not require Microsoft Office, WPS, an account, or an external conversion service.

## Why two engines?

A single renderer did not preserve both test cases that motivated this project. The ordinary authorization document benefited from docx4j's original-font line metrics. A form with a floating table and positioned images needed ONLYOFFICE plus a correction for declared document-grid spacing.

This is a practical compatibility layer, not a guarantee of pixel-identical output for every Word document. Font substitutions, complex section layouts, tracked changes, equations, and unusual embedded objects still require visual review. Page-count agreement alone does not establish fidelity.

## Behavior

- Keeps the uploaded source bytes unchanged and converts a private working copy.
- Resolves declared East Asian theme-font references for the shape renderer without changing text, font sizes, or image anchors.
- Restores document-grid height for eligible empty, automatically spaced body paragraphs using known font metrics. It preserves explicit line heights and skips ambiguous cases.
- Checks process status and validates the final PDF with Poppler before atomically publishing it.
- Produces a JSON report with the engine, structural features, normalizations, page count, elapsed time, and source/output hashes.
- Kills the renderer's process group on timeout.

The converter does not remove blank pages, shrink content to a target page count, or silently retry a failed renderer with another engine.

## Quick start

The packaged runtime passed the NAS acceptance tests below. The clean Docker image also passed both-renderer smoke tests as an unprivileged user with networking disabled and a read-only root filesystem.

Build the combined image on an x86-64 Linux host:

```sh
docker build -t docx-print-converter:0.1.0 .
mkdir -p output
docker run --rm --network none --memory 2g --pids-limit 192 \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" \
  docx-print-converter:0.1.0 /input/document.docx /output/document.pdf \
  --report /output/document.json
```

The current ONLYOFFICE package is Linux amd64. ARM hosts need an amd64 host or explicit emulation. Build downloads are pinned by version and checksum. Conversion itself needs no network.

With the renderer dependencies installed:

```sh
python3 -m pip install .
docx-print-convert input.docx output.pdf --report output.json
```

Choose an engine explicitly for comparison:

```sh
docx-print-convert input.docx output.pdf --engine docx4j
docx-print-convert input.docx output.pdf --engine onlyoffice
```

`--timeout` defaults to 180 seconds. Input is currently limited to DOCX, up to 128 MiB. The command returns nonzero on failure; an existing output file is preserved when conversion fails.

## Java backend

Java 17 and Maven are required to build the flow-layout backend:

```sh
cd java
mvn -B -ntp verify
```

The output is `java/target/docx-print-converter.jar`. The shaded JAR merges service-provider files needed by JAXB and the docx4j/FOP layout extensions.

The backend requires four unmodified Fandol font files:

- `FandolSong-Regular.otf`
- `FandolSong-Bold.otf`
- `FandolHei-Regular.otf`
- `FandolHei-Bold.otf`

Obtain them from the [official CTAN Fandol package](https://ctan.org/pkg/fandol). Place them under `/usr/share/fonts/opentype/fandol`, or set `DOCX_FONT_DIR` to another directory. `Dockerfile.java` provides a standalone backend build with a checksum-verified font download.

For direct backend testing:

```sh
DOCX_FONT_DIR=/path/to/fandol java -Djava.awt.headless=true -Xmx1g \
  -jar java/target/docx-print-converter.jar input.docx output.pdf
```

The Python router independently validates PDFs with `pdfinfo`. The Java backend performs only basic PDF-envelope checks, so direct use should also include a PDF parser and visual review.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `DOCX_PRINT_JAVA` | Java executable used by the router | `java` |
| `DOCX_PRINT_JAR` | Shaded flow-layout backend | `/opt/docx-print-converter/java/docx-print-converter.jar` |
| `DOCX_FONT_DIR` | Four required Fandol OTF files | `/usr/share/fonts/opentype/fandol` |
| `DOCX_PRINT_ONLYOFFICE_DIR` | ONLYOFFICE converter and generated font index | `/var/www/onlyoffice/documentserver/server/FileConverter/bin` |

The shape renderer needs its complete runtime directory structure, generated font indexes, and font mappings. Copying only the `x2t` executable is insufficient.

## Tests

```sh
python3 -m pip install pytest
python3 -m pytest
mvn -B -ntp -f java/pom.xml verify
```

The automated unit tests use generated public fixtures. The initial release passed 71 Python unit tests and 7 Java tests; the separate Docker acceptance workflow exercises both real renderers. Private customer documents, signatures, seals, credentials, and exported PDFs are excluded from this repository. Unit tests verify control flow and preservation rules; real renderer tests and page-image review are separate acceptance steps.

## Verified behavior

The initial acceptance run used unchanged private originals and PDFs exported by Word as references:

| Document class | Previous station | This converter | Checks |
|---|---:|---:|---|
| Authorization with CJK theme fonts and automatic line spacing | 2 pages | 1 page | Full text and signature/date areas preserved |
| Application form with a floating table, positioned images and document grid | 4 pages | 2 pages | Agreement starts on page 2; images and footer remain visible |

The router also passed generated ordinary-text, table, landscape, and intentional-blank-page probes. Private examples and their signatures are not distributed. These results cover the tested cases; they do not prove perfect fidelity for every DOCX.

## Deployment notes

Run the converter as an unprivileged user with a writable per-job directory, memory and process limits, and no outbound network access during conversion. Keep input read-only at the container boundary. Process one Office conversion at a time on a small NAS. Building dependencies needs network access; converting documents does not.

Do not expose the CLI as an unrestricted public file-conversion service. Any HTTP adapter needs request size limits, admission control, authentication appropriate to its environment, and job cleanup.

## Complete scan and print station

For the web interface, CUPS/SANE setup, scanning, preview and printer-job handling, see [NAS Office Station](https://github.com/aezizhu/nas-office-station). Its tested reference printer is the HP LaserJet MFP M437nda.

## License

The wrapper source is licensed under Apache License 2.0. Renderers and fonts retain their own licenses, including AGPLv3 for ONLYOFFICE Community. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before distributing a combined image. No proprietary Microsoft font files are included.
