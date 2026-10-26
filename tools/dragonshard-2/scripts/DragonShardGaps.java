// Enumerate the remaining static-analysis unknowns without guessing code or modifying a program.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import com.google.gson.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Iterator;

public class DragonShardGaps extends GhidraScript {
    private BufferedWriter writer;
    private Path directory;
    private long written, limit;
    private Gson json = new Gson();
    private JsonObject row(String kind) {
        JsonObject r = new JsonObject(); r.addProperty("kind", kind); return r;
    }
    private void emit(JsonObject r) throws IOException {
        String text = json.toJson(r) + "\n";
        long n = text.getBytes(StandardCharsets.UTF_8).length;
        if (n > limit - written || Files.getFileStore(directory).getUsableSpace() < n + 65536)
            throw new IOException("Gap-audit output budget/available space exhausted; partial evidence retained");
        writer.write(text); written += n;
    }
    private long ranges(AddressSetView set, String kind, boolean classify) throws Exception {
        long count = 0;
        for (AddressRange range : set.getAddressRanges(true)) {
            monitor.checkCancelled();
            JsonObject r = row(kind);
            r.addProperty("start", range.getMinAddress().toString());
            r.addProperty("end", range.getMaxAddress().toString());
            r.addProperty("bytes", range.getLength());
            if (classify) {
                boolean padding = true;
                StringBuilder prefix = new StringBuilder();
                byte[] buffer = new byte[65536]; long offset = 0;
                while (offset < range.getLength()) {
                    monitor.checkCancelled();
                    int size = (int)Math.min(buffer.length, range.getLength() - offset);
                    int read = currentProgram.getMemory().getBytes(range.getMinAddress().add(offset), buffer, 0, size);
                    if (read != size) throw new IOException("Short memory read during gap classification");
                    for (int i = 0; i < read; i++) {
                        int b = buffer[i] & 255;
                        if (b != 0 && b != 0x90 && b != 0xcc) padding = false;
                        if (offset + i < 32) prefix.append(String.format("%02x", b));
                    }
                    offset += read;
                }
                r.addProperty("prefix_bytes", prefix.toString());
                r.addProperty("only_00_90_cc_bytes", padding);
                // A padding-like byte pattern is evidence, never proof of unreachable/non-code.
            }
            emit(r); count++;
        }
        return count;
    }
    public void run() throws Exception {
        Program original = currentProgram, packedProgram = null;
        try {
            String path = System.getProperty("dragonshard.packed.path");
            if (path != null) {
                PackedDatabase packed = PackedDatabase.getPackedDatabase(new File(path), false, monitor);
                db.DBHandle handle = packed.open(monitor);
                try { packedProgram = new ProgramDB(handle, OpenMode.UPDATE, monitor, this); }
                catch (Exception e) { handle.close(); throw e; }
                currentProgram = packedProgram;
            }
            audit();
        } finally {
            currentProgram = original;
            if (packedProgram != null) packedProgram.release(this);
        }
    }
    private void audit() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 4) throw new IllegalArgumentException("output.jsonl sha256 snapshot max_bytes");
        if (!args[1].equalsIgnoreCase(currentProgram.getExecutableSHA256())) throw new IOException("Wrong binary");
        Path output = Paths.get(args[0]); directory = output.toAbsolutePath().getParent();
        Path partial = Paths.get(output.toString() + ".partial");
        if (Files.exists(output) || Files.exists(partial)) throw new IOException("Refusing evidence overwrite");
        limit = Long.parseLong(args[3]);
        if (limit < 1) throw new IllegalArgumentException("Output budget must be positive");
        Files.createDirectories(directory);
        try (BufferedWriter w = Files.newBufferedWriter(partial, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW)) {
            writer = w;
            JsonObject header = row("program");
            header.addProperty("sha256", args[1]); header.addProperty("snapshot_id", args[2]);
            header.addProperty("name", currentProgram.getName());
            header.add("analysis_options", json.toJsonTree(getCurrentAnalysisOptionsAndValues(currentProgram)));
            emit(header);
            AddressSet executable = new AddressSet(), instructions = new AddressSet(), data = new AddressSet(), bodies = new AddressSet();
            for (MemoryBlock block : currentProgram.getMemory().getBlocks())
                if (block.isInitialized() && block.isExecute()) executable.add(block.getStart(), block.getEnd());
            long functions = 0, missing = 0, unresolved = 0, synthetic = 0, bookmarks = 0;
            Listing listing = currentProgram.getListing();
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled(); functions++; bodies.add(function.getBody());
                if (listing.getInstructionAt(function.getEntryPoint()) != null) continue;
                Function target = function.isThunk() ? function.getThunkedFunction(true) : null;
                boolean external = target != null && target.isExternal();
                if (external) synthetic++; else missing++;
                JsonObject r = row("missing_entry_instruction");
                r.addProperty("address", function.getEntryPoint().toString());
                r.addProperty("name", function.getName(true));
                r.addProperty("synthetic_external_thunk", external); emit(r);
            }
            for (Instruction instruction : listing.getInstructions(executable, true)) {
                monitor.checkCancelled(); instructions.add(instruction.getMinAddress(), instruction.getMaxAddress());
                if (!instruction.getFlowType().isComputed() ||
                    !(instruction.getFlowType().isCall() || instruction.getFlowType().isJump())) continue;
                if (instruction.getFlows().length != 0) continue;
                unresolved++;
                JsonObject r = row("unresolved_computed_transfer");
                r.addProperty("address", instruction.getAddress().toString());
                r.addProperty("instruction", instruction.toString());
                r.addProperty("flow_type", instruction.getFlowType().toString());
                Function owner = currentProgram.getFunctionManager().getFunctionContaining(instruction.getAddress());
                if (owner != null) r.addProperty("function", owner.getEntryPoint().toString());
                JsonArray refs = new JsonArray();
                for (Reference reference : instruction.getReferencesFrom()) {
                    JsonObject edge = new JsonObject();
                    edge.addProperty("target", reference.getToAddress().toString());
                    edge.addProperty("type", reference.getReferenceType().toString()); refs.add(edge);
                }
                r.add("references", refs); emit(r);
            }
            for (Data item : listing.getDefinedData(executable, true)) {
                monitor.checkCancelled(); data.add(item.getMinAddress(), item.getMaxAddress());
            }
            AddressSet undefined = executable.subtract(instructions).subtract(data);
            AddressSet unowned = instructions.subtract(bodies);
            long undefinedRanges = ranges(undefined, "undefined_executable_range", true);
            long unownedRanges = ranges(unowned, "instruction_range_without_function", false);
            Iterator<Bookmark> iterator = currentProgram.getBookmarkManager().getBookmarksIterator();
            while (iterator.hasNext()) {
                monitor.checkCancelled(); Bookmark bookmark = iterator.next();
                if (!bookmark.getTypeString().equals("Error") && !bookmark.getTypeString().equals("Warning")) continue;
                JsonObject r = row("analysis_bookmark");
                r.addProperty("address", bookmark.getAddress().toString()); r.addProperty("type", bookmark.getTypeString());
                r.addProperty("category", bookmark.getCategory()); r.addProperty("comment", bookmark.getComment());
                emit(r); bookmarks++;
            }
            JsonObject end = row("complete"); end.addProperty("functions", functions);
            end.addProperty("missing_native_entry_instructions", missing);
            end.addProperty("synthetic_external_thunks", synthetic);
            end.addProperty("unresolved_computed_transfers", unresolved);
            end.addProperty("undefined_executable_bytes", undefined.getNumAddresses());
            end.addProperty("undefined_executable_ranges", undefinedRanges);
            end.addProperty("unowned_instruction_bytes", unowned.getNumAddresses());
            end.addProperty("unowned_instruction_ranges", unownedRanges);
            end.addProperty("error_warning_bookmarks", bookmarks); emit(end);
        }
        Files.move(partial, output, StandardCopyOption.ATOMIC_MOVE);
    }
}
