// Match all selected functions against local FID libraries without applying speculative names.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.feature.fid.service.*;
import ghidra.feature.fid.db.*;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import ghidra.program.model.listing.*;
import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class DragonShardFid extends GhidraScript {
    private long bytes;
    private void emit(BufferedWriter writer, JsonObject row, Path path, long cap) throws Exception {
        String text = new Gson().toJson(row) + "\n";
        long n = text.getBytes(StandardCharsets.UTF_8).length;
        if (bytes + n > cap || Files.getFileStore(path.getParent()).getUsableSpace() < n + 65536)
            throw new IOException("FID output budget exceeded; partial evidence preserved");
        writer.write(text); bytes += n;
    }
    public void run() throws Exception {
        Program original = currentProgram, packedProgram = null;
        try {
            String packedPath = System.getProperty("dragonshard.packed.path");
            if (packedPath != null) {
                PackedDatabase packed = PackedDatabase.getPackedDatabase(new File(packedPath), false, monitor);
                db.DBHandle handle = packed.open(monitor);
                try { packedProgram = new ProgramDB(handle, OpenMode.UPDATE, monitor, this); }
                catch (Exception e) { handle.close(); throw e; }
                currentProgram = packedProgram;
            }
            exportMatches();
        } finally {
            currentProgram = original;
            if (packedProgram != null) packedProgram.release(this);
        }
    }
    private void exportMatches() throws Exception {
        String[] a = getScriptArgs();
        if (a.length != 7) throw new IllegalArgumentException("output shard shards sha256 snapshot max_bytes expected_functions");
        Path output = Paths.get(a[0]), partial = Paths.get(a[0] + ".partial");
        int shard = Integer.parseInt(a[1]), shards = Integer.parseInt(a[2]);
        long cap = Long.parseLong(a[5]);
        if (shards < 1 || shard < 0 || shard >= shards || cap < 1) throw new IllegalArgumentException("Invalid partition/budget");
        if (!a[3].equalsIgnoreCase(currentProgram.getExecutableSHA256())) throw new IOException("Wrong binary");
        if (Files.exists(output) || Files.exists(partial)) throw new IOException("Refusing overwrite");
        Files.createDirectories(output.getParent());
        FidService service = new FidService();
        int total = 0, attempted = 0, matched = 0, errors = 0, unhashable = 0;
        try (FidQueryService query = service.openFidQueryService(currentProgram.getLanguage(), false);
             BufferedWriter writer = Files.newBufferedWriter(partial, StandardOpenOption.CREATE_NEW)) {
            FidProgramSeeker seeker = service.getProgramSeeker(currentProgram, query, service.getDefaultScoreThreshold());
            JsonObject header = new JsonObject();
            header.addProperty("kind", "program"); header.addProperty("sha256", a[3]);
            header.addProperty("snapshot_id", a[4]); header.addProperty("shard", shard); header.addProperty("shards", shards);
            header.addProperty("score_threshold", service.getDefaultScoreThreshold());
            header.addProperty("matches_are_candidates_not_identity_proof", true);
            JsonArray databases = new JsonArray();
            for (FidFile file : FidFileManager.getInstance().getFidFiles())
                if (file.isActive() && file.canProcessLanguage(currentProgram.getLanguage())) databases.add(file.getPath());
            if (databases.isEmpty()) throw new IOException("No active compatible FID databases");
            header.add("databases", databases); emit(writer, header, output, cap);
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled();
                int ordinal = total++;
                if (ordinal % shards != shard) continue;
                attempted++;
                JsonObject row = new JsonObject(); row.addProperty("kind", "function");
                row.addProperty("ordinal", ordinal); row.addProperty("address", function.getEntryPoint().toString());
                row.addProperty("name", function.getName(true));
                try {
                    FidSearchResult result = seeker.searchFunction(function, monitor);
                    JsonArray matches = new JsonArray();
                    if (result == null) unhashable++;
                    else {
                        row.addProperty("full_hash", Long.toUnsignedString(result.hashQuad.getFullHash(), 16));
                        row.addProperty("specific_hash", Long.toUnsignedString(result.hashQuad.getSpecificHash(), 16));
                        if (result.matches != null) for (FidMatch match : result.matches) {
                            JsonObject item = new JsonObject();
                            item.addProperty("name", match.getFunctionRecord().getName());
                            item.addProperty("library", match.getLibraryRecord().toString());
                            item.addProperty("score", match.getOverallScore());
                            item.addProperty("match_mode", match.getPrimaryFunctionMatchMode().toString());
                            item.addProperty("source_path", match.getFunctionRecord().getDomainPath());
                            matches.add(item);
                        }
                    }
                    row.addProperty("status", result == null ? "unhashable" : matches.isEmpty() ? "unmatched" : "candidate");
                    row.add("matches", matches);
                    if (!matches.isEmpty()) matched++;
                } catch (ghidra.program.model.mem.MemoryAccessException e) {
                    errors++; row.addProperty("status", "error"); row.addProperty("error", e.toString());
                }
                emit(writer, row, output, cap);
                if (attempted % 100 == 0) writer.flush();
            }
            if (total != Integer.parseInt(a[6])) throw new IOException("Function inventory changed");
            JsonObject end = new JsonObject(); end.addProperty("kind", "complete");
            end.addProperty("total_enumerated", total); end.addProperty("attempted", attempted);
            end.addProperty("matched_functions", matched); end.addProperty("errors", errors); end.addProperty("unhashable", unhashable);
            emit(writer, end, output, cap);
        }
        Files.move(partial, output, StandardCopyOption.ATOMIC_MOVE);
    }
}
