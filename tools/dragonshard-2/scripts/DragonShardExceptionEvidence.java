// Preserve disassembly/raw p-code and retry explicitly failed functions on isolated copies.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.time.Instant;

public class DragonShardExceptionEvidence extends GhidraScript {
    protected JsonArray prepare(Function function, JsonObject request) throws Exception {
        return new JsonArray();
    }
    private int timeout = 120, maxInstructions = 100000, payload = 50;
    private long maximumBytes = Long.MAX_VALUE, writtenBytes;
    private void saveJson(Path path, JsonObject object) throws Exception {
        byte[] bytes = (new Gson().toJson(object) + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);
        if (bytes.length > maximumBytes - writtenBytes ||
            Files.getFileStore(path.toAbsolutePath().getParent()).getUsableSpace() < bytes.length + 65536L)
            throw new IOException("Exception evidence output budget exhausted; previous evidence retained");
        Path partial = Paths.get(path.toString() + ".partial");
        Files.write(partial, bytes, StandardOpenOption.CREATE_NEW);
        Files.move(partial, path, StandardCopyOption.ATOMIC_MOVE);
        writtenBytes += bytes.length;
    }
    private JsonObject attempt(Function function, String style, JsonObject request) throws Exception {
        JsonObject out = new JsonObject();
        out.addProperty("style", style);
        out.addProperty("ordinary_decompilation", style.equals("decompile"));
        DecompInterface dec = new DecompInterface();
        try {
            DecompileOptions options = new DecompileOptions();
            options.grabFromProgram(currentProgram);
            options.setMaxInstructions(maxInstructions);
            options.setMaxPayloadMBytes(payload);
            dec.setOptions(options);
            if (!dec.setSimplificationStyle(style) || !dec.openProgram(currentProgram))
                throw new IOException("Cannot initialize decompiler: " + dec.getLastMessage());
            DecompileResults result = dec.decompileFunction(function, timeout, monitor);
            out.addProperty("completed", result.decompileCompleted());
            out.addProperty("timed_out", result.isTimedOut());
            out.addProperty("error", result.getErrorMessage());
            if (result.getDecompiledFunction() != null)
                out.addProperty("c", result.getDecompiledFunction().getC());
            if (request.has("indirect_sites") && result.decompileCompleted() && result.getHighFunction() != null)
                out.add("indirect_slice", DragonShardIndirect.slice(result.getHighFunction(),
                    request.getAsJsonArray("indirect_sites"), monitor));
        } finally { dec.dispose(); }
        return out;
    }
    public void run() throws Exception {
        Program original = currentProgram, packedProgram = null;
        String path = System.getProperty("dragonshard.packed.path");
        try {
            if (path != null) {
                PackedDatabase packed = PackedDatabase.getPackedDatabase(new File(path), false, monitor);
                db.DBHandle handle = packed.open(monitor);
                try { packedProgram = new ProgramDB(handle, OpenMode.UPDATE, monitor, this); }
                catch (Exception e) { handle.close(); throw e; }
                currentProgram = packedProgram;
            }
            exportEvidence();
        } finally {
            currentProgram = original;
            if (packedProgram != null) packedProgram.release(this);
        }
    }
    private void exportEvidence() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("request.json evidence.json");
        JsonObject request;
        try (Reader reader = Files.newBufferedReader(Paths.get(args[0]))) {
            request = JsonParser.parseReader(reader).getAsJsonObject();
        }
        if (!currentProgram.getExecutableSHA256().equalsIgnoreCase(request.get("sha256").getAsString()))
            throw new IllegalArgumentException("Wrong binary");
        if (request.has("timeout_seconds")) timeout = request.get("timeout_seconds").getAsInt();
        if (request.has("max_instructions")) maxInstructions = request.get("max_instructions").getAsInt();
        if (request.has("payload_mib")) payload = request.get("payload_mib").getAsInt();
        if (request.has("max_output_bytes")) maximumBytes = request.get("max_output_bytes").getAsLong();
        if (timeout < 1 || maxInstructions < 1 || payload < 1) throw new IllegalArgumentException("Invalid limits");
        Path output = Paths.get(args[1]);
        if (Files.exists(output)) throw new IOException("Refusing evidence overwrite");
        JsonObject report = new JsonObject();
        report.addProperty("sha256", currentProgram.getExecutableSHA256());
        report.addProperty("program", currentProgram.getName());
        report.addProperty("created", Instant.now().toString());
        report.addProperty("normalization_is_not_ordinary_C_success", true);
        report.add("snapshot_id", request.get("snapshot_id"));
        report.addProperty("timeout_seconds", timeout);
        report.addProperty("max_instructions", maxInstructions);
        report.addProperty("payload_mib", payload);
        JsonArray functions = new JsonArray();
        for (JsonElement entry : request.getAsJsonArray("addresses")) {
            monitor.checkCancelled();
            Address address = toAddr(entry.getAsString());
            Function function = currentProgram.getFunctionManager().getFunctionAt(address);
            if (function == null) throw new IOException("Missing function " + address);
            long originalBodyBytes = function.getBody().getNumAddresses();
            JsonArray preparation = prepare(function, request);
            JsonObject record = new JsonObject();
            record.addProperty("address", address.toString());
            record.addProperty("name", function.getName(true));
            record.addProperty("signature", function.getPrototypeString(false, false));
            record.addProperty("body_bytes", function.getBody().getNumAddresses());
            record.addProperty("original_body_bytes", originalBodyBytes);
            record.add("preparation", preparation);
            JsonArray instructions = new JsonArray();
            for (Instruction ins : currentProgram.getListing().getInstructions(function.getBody(), true)) {
                monitor.checkCancelled();
                JsonObject row = new JsonObject();
                row.addProperty("address", ins.getAddress().toString());
                row.addProperty("assembly", ins.toString());
                StringBuilder bytes = new StringBuilder();
                for (byte b : ins.getBytes()) bytes.append(String.format("%02x", b & 255));
                row.addProperty("bytes", bytes.toString());
                JsonArray pcode = new JsonArray();
                for (PcodeOp op : ins.getPcode()) pcode.add(op.toString());
                row.add("raw_pcode", pcode);
                instructions.add(row);
            }
            record.add("instructions", instructions);
            // Save the irreplaceable low-level evidence before a large native decompiler runs.
            Path assembly = Paths.get(output.toString() + "." + address + ".assembly.json");
            saveJson(assembly, record);
            record.add("standard_retry", attempt(function, "decompile", request));
            record.add("normalization_attempt", attempt(function, "normalize", request));
            functions.add(record);
        }
        report.add("functions", functions);
        report.addProperty("complete", true);
        saveJson(output, report);
        println("EXCEPTION_EVIDENCE complete " + functions.size());
    }
}
