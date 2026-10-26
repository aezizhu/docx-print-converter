# Lessons from preserving DOCX print layout

This document records reusable findings behind the converter, with synthetic examples and public source links. It contains no customer documents, identifying document hashes, screenshots, internal addresses or raw operational logs.

The implementation was validated with the pinned engine versions in this repository. It is a practical compatibility layer, not a claim that either engine reproduces every Word feature. Follow the linked tests to distinguish a verified property from a general limitation.

## 1. Page count is a necessary check, not a layout oracle

A PDF can have the expected number of pages while placing a heading over a footer, moving an image, or dropping content. Choosing whichever engine produces fewer pages can select the worse result.

Validate in layers: process exit status, PDF parsing, actual page count, text completeness, then full-page visual inspection. For known regression fixtures, raster comparison adds useful evidence. Text extraction order can differ between PDF libraries; a different extraction order is not automatically missing content, and equal extracted text is not proof that the page is legible.

Keep intentional blank pages. Do not infer an authoritative page count from `docProps/app.xml`: it is saved metadata that may be stale. The [public integration probes](../tests/integration/make_samples.py) include an intentionally blank middle page, landscape output and a table.

## 2. Font glyphs and line metrics are separate inputs

A substitute can cover every character and still produce different line heights. Small differences accumulate over a page, pushing a signature area or trailing paragraph onto another sheet. Nominal point size alone is not the line height.

The flow backend uses open Fandol glyphs while registering localized family names with docx4j's original-font metric catalog. Without the alias, a localized family name can miss the intended metric entry. This keeps the distinction between choosing printable glyphs and measuring the document's requested font.

See [the Java backend](../java/src/main/java/io/github/aezizhu/docxprint/Main.java) and [docx4j's versioned layout rules](https://github.com/plutext/docx4j/blob/VERSION_17_3_3/docx4j-export-fo/docs/word-layout-rules.md), especially its discussion of substituted-font line height. Do not modify font metrics merely until a sample fits a desired page count, or copy proprietary fonts into a public image to hide the mismatch.

## 3. A theme's empty East Asian slot may still specify a font

Consider a generated document that declares an East Asian theme reference, has an empty base East Asian typeface, and supplies a `Hans` script mapping. The document's declared theme language determines which supplemental mapping applies. Falling back directly to the Latin face can produce per-character CJK fallback with different metrics.

This class is described in [ONLYOFFICE's upstream script-font issue](https://github.com/ONLYOFFICE/DocumentServer/issues/1415). The compatibility pass resolves the declared relationship and language, then materializes only the corresponding font attribute in a temporary conversion copy. It leaves text, sizes, spacing and drawing anchors untouched. Ambiguous or unsupported mappings are not guessed.

See [theme resolution](../src/docx_print_converter/docx_compat.py) and [its preservation tests](../tests/test_docx_compat.py). Rewriting XML must also preserve namespace prefixes referenced by values such as `mc:Ignorable`; a seemingly harmless serialization can otherwise change compatibility behavior.

## 4. An empty paragraph can determine where the next page begins

Empty paragraphs still have paragraph-mark fonts, sizes and spacing. A floating image anchored to an empty paragraph does not make that paragraph disappear from the document flow.

For an eligible grid-aligned paragraph, the relevant relationship is:

```text
resolved height = ceil(natural line height / grid pitch) * grid pitch
```

As a synthetic example, a natural height of 18.2 pt on a 15.6 pt grid occupies 31.2 pt. Using the nominal font size instead of the line metrics can incorrectly choose one grid unit and pull following content upward.

Explicit exact spacing must remain exact. The [OOXML line-pitch documentation](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.docgrid.linepitch?view=openxml-3.0.1) describes that override. The converter deliberately narrows its correction to eligible empty, automatically spaced body paragraphs with known metrics. It skips table paragraphs, numbered paragraphs, text, fields, breaks, inline images, unsupported grids and unresolved styles. It does not remove the grid from the document.

See [grid normalization](../src/docx_print_converter/docx_grid.py) and [edge-case tests](../tests/test_docx_grid.py). An empty-looking numbered paragraph can still have a visible label and must not be treated as blank.

## 5. Route by structure, and follow package relationships

A renderer that performs well on ordinary text may mishandle floating tables or legacy shapes. The project uses docx4j for flow documents and ONLYOFFICE for structural features such as floating tables, text frames, VML shapes and anchored drawings.

That is an engineering choice supported by the tested cases, not a universal quality ranking. Do not route by a customer filename, document hash, company name or expected number of pages.

Also, a DOCX main part does not have to live at `word/document.xml`. Read the OPC relationships and inspect related parts, including headers, instead of assuming a directory. A relocated main part containing a floating table is a regression case in [routing tests](../tests/test_converter.py). The [router](../src/docx_print_converter/converter.py) records detected features so its choice is inspectable.

## 6. A newer executable does not prove the new renderer handled the job

A resident converter listener can remain on an old version while a new command-line executable is installed beside it. A fallback that reaches the same old engine does not provide independent layout coverage. Replacing an engine can also trade overflow for overlapping content.

Select the intended renderer explicitly, give work an isolated temporary directory, and inspect the actual output. In a listener-based integration, separate listener output from the accepted final PDF: a late listener response must not overwrite a fallback's result.

The CLI supports an explicit `--engine` for comparison. It does not silently retry a failed selected renderer with another engine. The station's [conversion adapter](https://github.com/aezizhu/nas-office-station/blob/main/app/office_conversion.py) separately validates the report against the input and PDF.

## 7. Publish output only after validating the artifact

A process can fail after writing a nonempty PDF. Conversely, a process can exit successfully without producing a usable PDF. Neither a file's existence nor a success-shaped stdout message is enough.

Render into a private staging directory, require the expected exit status, parse the output with an independent PDF tool, verify source preservation, and atomically replace the destination only after all checks pass. Preserve an existing output when conversion fails. A caller should cross-check reported source hash, output hash and page count against the actual files it received.

See [Python artifact tests](../tests/test_converter.py) and [Java publication tests](../java/src/test/java/io/github/aezizhu/docxprint/MainTest.java). Source hashes are useful for local verification but are document identifiers, not an anonymization technique for public bug reports.

## 8. Bound archive inspection before opening the ZIP directory

Limiting one XML entry does not bound the memory needed to open a ZIP archive. A ZIP library can eagerly construct objects for the complete central directory before the entry-size check runs.

The inspector checks directory size and entry count before constructing `ZipFile`, bounds XML reads, rejects entity declarations, and avoids following external package relationships. A boundary case matters: a maximum-length ZIP comment can hide a preceding ZIP64 locator if the tail read reserves space for only the ordinary end record and comment.

See [the bounded archive reader](../src/docx_print_converter/archive.py). These are inspection limits, not a complete hostile-file sandbox. Run the full renderer with resource limits, restricted filesystem access and no outbound network access; the documented Docker invocation provides that boundary.

## 9. Cancellation must reach the renderer

If the CLI and its renderer live in different process groups, killing the CLI can leave a JVM or native converter behind. Ordinary request-timeout handling alone does not cover external cancellation.

The runner cleans the renderer group on timeout, SIGTERM and SIGINT. On Linux, a parent-death signal also terminates the direct renderer after abrupt CLI death. That signal is not inherited by grandchildren, so container supervision remains important. The isolated launcher avoids doing Python work in a forked `preexec_fn` inside a multithreaded service.

See [the runner and its stated boundaries](../src/docx_print_converter/converter.py) and [cancellation regressions](../tests/test_converter.py). Verify that failed jobs leave no active renderer before increasing concurrency.

## 10. Package and test the exact runtime you will ship

A Java program can compile while a shaded JAR loses service-provider declarations needed by JAXB or the layout engine. A reduced Java runtime can start but lack modules required by image or XML paths. A native converter can require sibling libraries, SDK scripts and generated font indexes; copying its executable alone is insufficient.

The [Maven build](../java/pom.xml) merges service resources, and the [Docker build](../Dockerfile) keeps the native runtime layout and verifies downloaded artifacts. Generated font indexes contain paths, so relocating fonts requires verifying or regenerating those indexes. The alias preparation changes generated family-name metadata, not font binaries.

The [Docker acceptance workflow](../.github/workflows/docker.yml) invokes both real engines as an unprivileged user, with no network, a read-only root filesystem, and writable temporary storage. Unit tests using fake converters remain useful, but they cannot establish real layout fidelity. Check the exact product and edition as well: ONLYOFFICE Community's conversion service and the standalone Document Builder product are different offerings; one product's restrictions do not establish another's behavior. Keep the [third-party notices](../THIRD_PARTY_NOTICES.md) with the runtime.

## Sharing a useful reproduction

Generate the smallest document that expresses the failing structure: a theme-font reference, one floating table, one grid-aligned empty paragraph, or one deliberate page break. Keep generated expected text simple and inspect the whole result.

Do not attach business documents, their hashes, real filenames, signatures, seals, internal paths, raw logs or printer serial numbers. Describe the structural feature, pinned engine version, selected route and observed failure. A sanitized expected/actual example should be created from synthetic content, not by lightly editing a customer document.

The complete station's [lessons learned](https://github.com/aezizhu/nas-office-station/blob/main/docs/lessons-learned.md) cover resolution, scanner geometry, physical-output evidence and job ownership.
