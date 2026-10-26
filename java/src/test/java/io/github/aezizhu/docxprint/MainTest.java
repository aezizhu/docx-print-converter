package io.github.aezizhu.docxprint;

import static org.junit.jupiter.api.Assertions.*;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class MainTest {
    @TempDir Path directory;
    private static final byte[] PDF = "%PDF-1.4\nTest wrapper output\n%%EOF\n".getBytes(StandardCharsets.US_ASCII);

    private Path input() throws IOException {
        return Files.writeString(directory.resolve("source.docx"), "Source bytes for wrapper tests");
    }

    @Test void rejectsInvalidArguments() {
        ByteArrayOutputStream errors = new ByteArrayOutputStream();
        int result = Main.run(new String[0], System.out, new PrintStream(errors), (input, output) -> fail("Renderer ran"));
        assertEquals(2, result);
        assertTrue(errors.toString(StandardCharsets.UTF_8).contains("Usage:"));
    }

    @Test void publishesOnlyAfterSuccessfulRendering() throws Exception {
        Path source = input();
        Path output = directory.resolve("result.pdf");
        String before = Main.sha256(source);
        Main.Result result = Main.convert(source, output, (input, stream) -> {
            assertFalse(Files.exists(output));
            stream.write(PDF);
            assertFalse(Files.exists(output));
        });
        assertArrayEquals(PDF, Files.readAllBytes(output));
        assertEquals(before, Main.sha256(source));
        assertEquals(before, result.sourceSha256());
        assertEquals(PDF.length, result.bytes());
    }

    @Test void failurePreservesExistingOutputAndRemovesTemporaryFile() throws Exception {
        Path source = input();
        Path output = Files.writeString(directory.resolve("result.pdf"), "Existing output");
        assertThrows(IOException.class, () -> Main.convert(source, output, (input, stream) -> {
            stream.write(PDF);
            throw new IOException("Deliberate renderer failure");
        }));
        assertEquals("Existing output", Files.readString(output));
        try (var files = Files.list(directory)) { assertEquals(2, files.count()); }
    }

    @Test void rejectsOutputAliasingInputBeforeRendering() throws Exception {
        Path source = input();
        Path alias = directory.resolve("alias.pdf");
        Files.createLink(alias, source);
        assertThrows(IllegalArgumentException.class, () -> Main.convert(source, alias,
                (input, stream) -> fail("Renderer ran with aliased paths")));
    }

    @Test void rejectsSamePathBeforeRendering() throws Exception {
        Path source = input();
        assertThrows(IllegalArgumentException.class, () -> Main.convert(source, source,
                (input, stream) -> fail("Renderer ran with equal paths")));
    }

    @Test void rejectsChangedSourceAndDoesNotPublish() throws Exception {
        Path source = input();
        Path output = directory.resolve("result.pdf");
        assertThrows(IOException.class, () -> Main.convert(source, output, (input, stream) -> {
            stream.write(PDF);
            Files.writeString(input, "Unexpected source mutation");
        }));
        assertFalse(Files.exists(output));
    }

    @Test void rejectsEmptyOrIncompletePdf() throws Exception {
        Path source = input();
        for (byte[] content : new byte[][] {new byte[0], "%PDF-1.4\npartial".getBytes(StandardCharsets.US_ASCII)}) {
            Path output = directory.resolve("result.pdf");
            assertThrows(IOException.class, () -> Main.convert(source, output, (input, stream) -> stream.write(content)));
            assertFalse(Files.exists(output));
        }
    }
}
