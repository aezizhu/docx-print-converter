package io.github.aezizhu.docxprint;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.Locale;
import org.docx4j.Docx4J;
import org.docx4j.fonts.IdentityPlusMapper;
import org.docx4j.fonts.Mapper;
import org.docx4j.fonts.PhysicalFont;
import org.docx4j.fonts.PhysicalFonts;
import org.docx4j.openpackaging.packages.WordprocessingMLPackage;

/** Local DOCX conversion backend. Layout acceptance belongs to the calling workflow. */
public final class Main {
    private Main() { }

    @FunctionalInterface
    interface Renderer {
        void render(Path input, OutputStream output) throws Exception;
    }

    record Result(long bytes, double elapsedSeconds, String sourceSha256, String pdfSha256) { }

    public static void main(String[] args) {
        System.setProperty("java.awt.headless", "true");
        System.exit(run(args, System.out, System.err, Main::render));
    }

    static int run(String[] args, PrintStream out, PrintStream err, Renderer renderer) {
        if (args.length == 1 && "--help".equals(args[0])) {
            out.println("Usage: docx-print-converter INPUT.docx OUTPUT.pdf");
            return 0;
        }
        if (args.length != 2) {
            err.println("Usage: docx-print-converter INPUT.docx OUTPUT.pdf");
            return 2;
        }
        try {
            Result result = convert(Path.of(args[0]), Path.of(args[1]), renderer);
            out.printf(Locale.ROOT,
                    "{\"status\":\"converted\",\"renderer\":\"docx4j-17.3.3\",\"bytes\":%d,"
                    + "\"elapsed_seconds\":%.3f,\"source_sha256\":\"%s\",\"pdf_sha256\":\"%s\"}%n",
                    result.bytes(), result.elapsedSeconds(), result.sourceSha256(), result.pdfSha256());
            return 0;
        } catch (IllegalArgumentException exception) {
            err.println("Invalid arguments: " + diagnostic(exception));
            return 2;
        } catch (Exception exception) {
            err.println("Conversion failed: " + diagnostic(exception));
            return 1;
        }
    }

    private static String diagnostic(Exception exception) {
        String text = exception.getClass().getSimpleName() + ": " + String.valueOf(exception.getMessage());
        text = text.replace('\n', ' ').replace('\r', ' ');
        return text.substring(0, Math.min(text.length(), 500));
    }

    static Result convert(Path input, Path output, Renderer renderer) throws Exception {
        long started = System.nanoTime();
        Path source = input.toRealPath();
        Path destination = output.toAbsolutePath().normalize();
        if (!Files.isRegularFile(source) || !Files.isReadable(source)) {
            throw new IllegalArgumentException("Input must be a readable regular file");
        }
        if (source.equals(destination) || (Files.exists(destination) && Files.isSameFile(source, destination))) {
            throw new IllegalArgumentException("Input and output must be different files");
        }
        if (!source.getFileName().toString().toLowerCase(Locale.ROOT).endsWith(".docx")) {
            throw new IllegalArgumentException("Input must have the .docx extension");
        }
        if (destination.getFileName() == null || Files.isDirectory(destination)
                || !destination.getFileName().toString().toLowerCase(Locale.ROOT).endsWith(".pdf")) {
            throw new IllegalArgumentException("Output must be a .pdf file path");
        }
        Files.createDirectories(destination.getParent());
        destination = destination.getParent().toRealPath().resolve(destination.getFileName());
        if (source.equals(destination) || (Files.exists(destination) && Files.isSameFile(source, destination))) {
            throw new IllegalArgumentException("Resolved input and output must be different files");
        }
        String before = sha256(source);
        Path staged = Files.createTempFile(destination.getParent(), ".docxprint-", ".pdf");
        try {
            try (OutputStream stream = Files.newOutputStream(staged)) {
                renderer.render(source, stream);
            }
            validatePdfEnvelope(staged);
            if (!before.equals(sha256(source))) {
                throw new IOException("Source bytes changed during conversion; output was not published");
            }
            long bytes = Files.size(staged);
            String outputHash = sha256(staged);
            // Same-directory staging permits an atomic replacement. If the filesystem
            // cannot provide it, fail and retain the existing destination.
            Files.move(staged, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            return new Result(bytes, (System.nanoTime() - started) / 1_000_000_000.0, before, outputHash);
        } finally {
            Files.deleteIfExists(staged);
        }
    }

    static String sha256(Path file) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
        byte[] buffer = new byte[64 * 1024];
        try (InputStream stream = Files.newInputStream(file)) {
            int count;
            while ((count = stream.read(buffer)) != -1) {
                digest.update(buffer, 0, count);
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static void validatePdfEnvelope(Path pdf) throws IOException {
        try (RandomAccessFile file = new RandomAccessFile(pdf.toFile(), "r")) {
            long size = file.length();
            byte[] magic = new byte[5];
            if (size < magic.length) {
                throw new IOException("Renderer produced empty or truncated output");
            }
            file.readFully(magic);
            if (!Arrays.equals(magic, "%PDF-".getBytes(StandardCharsets.US_ASCII))) {
                throw new IOException("Renderer output has no PDF header");
            }
            byte[] tail = new byte[(int) Math.min(size, 1024)];
            file.seek(size - tail.length);
            file.readFully(tail);
            if (!new String(tail, StandardCharsets.ISO_8859_1).contains("%%EOF")) {
                throw new IOException("Renderer output has no PDF end marker");
            }
        }
    }

    private static void mapFamily(Mapper mapper, String target, String... names) {
        PhysicalFont font = PhysicalFonts.get(target);
        if (font == null) {
            throw new IllegalStateException("Required font family could not be registered: " + target);
        }
        for (String name : names) {
            mapper.put(name, font);
        }
    }

    private static void render(Path source, OutputStream output) throws Exception {
        PhysicalFonts.discoverPhysicalFonts();
        Path fontDirectory = Path.of(System.getenv().getOrDefault(
                "DOCX_FONT_DIR", "/usr/share/fonts/opentype/fandol"));
        for (String filename : new String[] {"FandolSong-Regular.otf", "FandolSong-Bold.otf",
                "FandolHei-Regular.otf", "FandolHei-Bold.otf"}) {
            Path font = fontDirectory.resolve(filename);
            if (!Files.isRegularFile(font)) {
                throw new IOException("Required font is unavailable: " + filename + "; set DOCX_FONT_DIR");
            }
            PhysicalFonts.addPhysicalFont(font.toUri());
        }
        Mapper mapper = new IdentityPlusMapper();
        // Localized family names refer to the existing docx4j metrics tables.
        // These aliases do not change any document size, spacing or font file.
        mapper.registerLineMetricsAlias("宋体", "SimSun");
        mapper.registerLineMetricsAlias("新宋体", "NSimSun");
        mapper.registerLineMetricsAlias("黑体", "SimHei");
        mapper.registerLineMetricsAlias("微软雅黑", "Microsoft YaHei");
        mapFamily(mapper, "FandolSong", "SimSun", "NSimSun", "宋体", "新宋体");
        mapFamily(mapper, "FandolHei", "SimHei", "黑体", "Microsoft YaHei", "微软雅黑");
        WordprocessingMLPackage document = WordprocessingMLPackage.load(source.toFile());
        document.setFontMapper(mapper);
        Docx4J.toPDF(document, output);
    }
}
