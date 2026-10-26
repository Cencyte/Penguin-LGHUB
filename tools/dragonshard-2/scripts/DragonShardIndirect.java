// Preserve SSA/data-flow slices behind unresolved dispatch sites, without inventing call targets.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Program;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.SourceType;
import ghidra.util.task.TaskMonitor;
import com.google.gson.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class DragonShardIndirect extends GhidraScript {
    private BufferedWriter writer;
    private Path directory;
    private long written, limit;
    private final Gson json = new Gson();
    private void emit(JsonObject row) throws IOException {
        String text = json.toJson(row) + "\n"; long bytes = text.getBytes(StandardCharsets.UTF_8).length;
        if (bytes > limit - written || Files.getFileStore(directory).getUsableSpace() < bytes + 65536)
            throw new IOException("Indirect-evidence budget/space exhausted; partial evidence retained");
        writer.write(text); written += bytes;
    }
    private static int identify(Varnode node, IdentityHashMap<Varnode, Integer> ids, ArrayList<Varnode> nodes) {
        Integer existing = ids.get(node);
        if (existing != null) return existing;
        int id = nodes.size(); ids.put(node, id); nodes.add(node); return id;
    }
    public static JsonObject slice(HighFunction high, JsonArray sites, TaskMonitor monitor) throws Exception {
        Set<String> wanted = new HashSet<>(), found = new HashSet<>();
        for (JsonElement site : sites) wanted.add(site.getAsString());
        IdentityHashMap<Varnode, Integer> ids = new IdentityHashMap<>();
        ArrayList<Varnode> nodes = new ArrayList<>();
        JsonArray transfers = new JsonArray();
        JsonArray operations = new JsonArray();
        Iterator<PcodeOpAST> iterator = high.getPcodeOps();
        while (iterator.hasNext()) {
            monitor.checkCancelled(); PcodeOpAST op = iterator.next();
            String address = op.getSeqnum().getTarget().toString();
            int code = op.getOpcode();
            if (wanted.contains(address)) {
                JsonObject observation = new JsonObject();
                observation.addProperty("site", address);
                observation.addProperty("opcode", op.getMnemonic());
                observation.addProperty("operation", op.toString());
                operations.add(observation);
            }
            if (!wanted.contains(address) || (code != PcodeOp.CALLIND && code != PcodeOp.BRANCHIND &&
                                             code != PcodeOp.CALL && code != PcodeOp.BRANCH)) continue;
            JsonObject transfer = new JsonObject();
            transfer.addProperty("site", address); transfer.addProperty("opcode", op.getMnemonic());
            transfer.addProperty("target_node", identify(op.getInput(0), ids, nodes));
            transfer.addProperty("direct_in_decompiler", code == PcodeOp.CALL || code == PcodeOp.BRANCH);
            transfers.add(transfer); found.add(address);
        }
        JsonArray graph = new JsonArray();
        // Shared node IDs preserve cycles/phi nodes; traverse the complete backward closure once.
        for (int cursor = 0; cursor < nodes.size(); cursor++) {
            monitor.checkCancelled(); Varnode node = nodes.get(cursor);
            JsonObject record = new JsonObject(); record.addProperty("id", cursor);
            record.addProperty("space", node.getAddress().getAddressSpace().getName());
            record.addProperty("offset", Long.toUnsignedString(node.getOffset(), 16));
            record.addProperty("size", node.getSize()); record.addProperty("constant", node.isConstant());
            record.addProperty("input", node.isInput());
            PcodeOp definition = node.getDef();
            if (definition != null) {
                record.addProperty("definition_site", definition.getSeqnum().getTarget().toString());
                record.addProperty("opcode", definition.getMnemonic());
                JsonArray inputs = new JsonArray();
                for (int i = 0; i < definition.getNumInputs(); i++)
                    inputs.add(identify(definition.getInput(i), ids, nodes));
                record.add("inputs", inputs);
            }
            graph.add(record);
        }
        JsonObject result = new JsonObject(); result.add("transfers", transfers); result.add("nodes", graph);
        result.add("operations_at_requested_sites", operations);
        JsonArray absent = new JsonArray();
        for (String site : new TreeSet<>(wanted)) if (!found.contains(site)) absent.add(site);
        result.add("sites_without_matching_high_pcode", absent);
        result.addProperty("runtime_target_proof", false);
        return result;
    }
    private JsonArray instructions(JsonArray sites, Function function) throws Exception {
        JsonArray result = new JsonArray();
        for (JsonElement site : sites) {
            monitor.checkCancelled();
            JsonObject row = new JsonObject(); row.add("site", site);
            Address address = toAddr(site.getAsString());
            Instruction ins = currentProgram.getListing().getInstructionAt(address);
            row.addProperty("inside_function_body", function.getBody().contains(address));
            if (ins != null) {
                row.addProperty("assembly", ins.toString());
                row.addProperty("bytes", HexFormat.of().formatHex(ins.getBytes()));
                row.addProperty("flow", ins.getFlowType().toString());
                row.addProperty("flow_override", ins.getFlowOverride().toString());
                JsonArray raw = new JsonArray();
                for (PcodeOp op : ins.getPcode()) raw.add(op.toString());
                row.add("raw_pcode", raw);
            }
            result.add(row);
        }
        return result;
    }
    private JsonObject context(String text) throws Exception {
        Address site = toAddr(text);
        JsonObject row = new JsonObject(); row.addProperty("kind", "site_context"); row.addProperty("site", text);
        Function owner = getFunctionContaining(site);
        if (owner != null) row.addProperty("function", owner.getEntryPoint().toString());
        Instruction start = getInstructionAt(site);
        if (start == null) throw new IOException("No instruction at requested context site " + site);
        for (int i=0; i<20; i++) {
            Instruction previous = currentProgram.getListing().getInstructionBefore(start.getAddress());
            if (previous == null || start.getAddress().subtract(previous.getAddress()) > 16) break;
            start = previous;
        }
        JsonArray listing = new JsonArray();
        for (Instruction ins : currentProgram.getListing().getInstructions(start.getAddress(), true)) {
            monitor.checkCancelled();
            if (ins.getAddress().compareTo(site.add(24)) > 0) break;
            JsonObject entry = new JsonObject(); entry.addProperty("address", ins.getAddress().toString());
            entry.addProperty("assembly", ins.toString());
            entry.addProperty("bytes", HexFormat.of().formatHex(ins.getBytes()));
            Function at = getFunctionAt(ins.getAddress());
            if (at != null) { entry.addProperty("function_entry", at.getEntryPoint().toString()); entry.addProperty("function_name", at.getName(true)); }
            JsonArray refs = new JsonArray();
            var iterator = currentProgram.getReferenceManager().getReferencesTo(ins.getAddress());
            while(iterator.hasNext()) {
                var ref=iterator.next(); JsonObject r=new JsonObject();
                r.addProperty("from",ref.getFromAddress().toString()); r.addProperty("type",ref.getReferenceType().toString()); refs.add(r);
            }
            entry.add("incoming",refs);
            JsonArray raw=new JsonArray(); for(PcodeOp op:ins.getPcode()) raw.add(op.toString());
            entry.add("raw_pcode",raw); listing.add(entry);
        }
        row.add("instructions",listing); return row;
    }
    private JsonArray prepareRegions(JsonObject request) throws Exception {
        JsonArray changes = new JsonArray();
        if (!request.has("reviewed_regions") || request.getAsJsonArray("reviewed_regions").isEmpty()) return changes;
        if (System.getProperty("dragonshard.packed.path") == null)
            throw new IOException("Region recovery requires a private packed-program view");
        int tx=currentProgram.startTransaction("Reviewed unwind region recovery"); boolean ok=false;
        try {
            for(JsonElement element:request.getAsJsonArray("reviewed_regions")) {
                JsonObject spec=element.getAsJsonObject(); Address entry=toAddr(spec.get("entry").getAsString());
                Address start=toAddr(spec.get("start").getAsString());
                byte[] expected=HexFormat.of().parseHex(spec.get("bytes").getAsString());
                if(expected.length==0)throw new IOException("Empty region");
                byte[] actual=new byte[expected.length];
                if(currentProgram.getMemory().getBytes(start,actual)!=actual.length || !Arrays.equals(expected,actual))
                    throw new IOException("Reviewed region bytes changed");
                Function function=getFunctionAt(entry);
                AddressSet body=function==null?new AddressSet():new AddressSet(function.getBody());
                long before=body.getNumAddresses();
                AddressSet region=new AddressSet(start,start.add(expected.length-1));
                for(Instruction ins:currentProgram.getListing().getInstructions(region,true)) {
                    Function owner=getFunctionContaining(ins.getAddress());
                    if(owner!=null && !owner.equals(function))throw new IOException("Region overlaps another function");
                    body.add(ins.getMinAddress(),ins.getMaxAddress());
                }
                if(!body.contains(entry))throw new IOException("No entry instruction in reviewed region");
                if(function==null)
                    function=currentProgram.getFunctionManager().createFunction("Recovered_"+entry,entry,body,SourceType.ANALYSIS);
                else function.setBody(body);
                JsonObject change=spec.deepCopy();change.addProperty("original_body_bytes",before);
                change.addProperty("recovered_body_bytes",body.getNumAddresses());
                change.addProperty("changes_only_in_private_program",true);changes.add(change);
            }
            ok=true;
        } finally {currentProgram.endTransaction(tx,ok);}
        return changes;
    }
    private JsonObject normalize(Function function, JsonArray sites, JsonObject request) throws Exception {
        DecompInterface dec = new DecompInterface();
        try {
            DecompileOptions options = new DecompileOptions(); options.grabFromProgram(currentProgram);
            options.setMaxInstructions(100000); options.setMaxPayloadMBytes(64);
            String style = request.has("recovery_style") ? request.get("recovery_style").getAsString() : "normalize";
            if (!Set.of("normalize", "firstpass", "register").contains(style))
                throw new IOException("Unsupported recovery style " + style);
            boolean retain = request.has("retain_unreachable") && request.get("retain_unreachable").getAsBoolean();
            if (retain) options.setEliminateUnreachable(false);
            dec.setOptions(options); dec.toggleCCode(request.has("reviewed_regions") && !request.getAsJsonArray("reviewed_regions").isEmpty()); dec.toggleSyntaxTree(true);
            if (!dec.setSimplificationStyle(style) || !dec.openProgram(currentProgram))
                throw new IOException("Cannot initialize normalization: " + dec.getLastMessage());
            DecompileResults result = dec.decompileFunction(function, 120, monitor);
            JsonObject row = new JsonObject(); row.addProperty("style", style);
            row.addProperty("retain_unreachable", retain);
            row.addProperty("completed", result.decompileCompleted());
            row.addProperty("error", result.getErrorMessage());
            if(result.getDecompiledFunction()!=null) row.addProperty("c",result.getDecompiledFunction().getC());
            if (result.decompileCompleted() && result.getHighFunction() != null)
                row.add("slice", slice(result.getHighFunction(), sites, monitor));
            return row;
        } finally { dec.dispose(); }
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String packedPath=System.getProperty("dragonshard.packed.path");
        Program original=currentProgram, packedProgram=null;
        if(packedPath!=null) {
            PackedDatabase packed=PackedDatabase.getPackedDatabase(new File(packedPath),false,monitor);
            db.DBHandle handle=null;
            try {
                handle=packed.open(monitor);
                packedProgram=new ProgramDB(handle,OpenMode.UPDATE,monitor,this);
                currentProgram=packedProgram;
            } catch(Exception e) {
                if(handle!=null) handle.close(); else packed.dispose();
                throw e;
            }
        }
        try { analyze(args); }
        finally {
            currentProgram=original;
            if(packedProgram!=null) packedProgram.release(this);
        }
    }
    private void analyze(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("request.json output.jsonl max_bytes");
        JsonObject request;
        try (Reader reader = Files.newBufferedReader(Paths.get(args[0]))) {
            request = JsonParser.parseReader(reader).getAsJsonObject();
        }
        if (!request.get("sha256").getAsString().equalsIgnoreCase(currentProgram.getExecutableSHA256()))
            throw new IOException("Wrong binary");
        JsonArray preparation=prepareRegions(request);
        int scanned=0, enumerated=0;
        if(request.has("scan_shards")) {
            int shards=request.get("scan_shards").getAsInt(), shard=request.get("scan_shard").getAsInt();
            if(shards<1 || shard<0 || shard>=shards || request.has("functions"))
                throw new IllegalArgumentException("Invalid scan partition or mixed selection modes");
            JsonArray selected=new JsonArray();
            for(Function function:currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled();
                if(enumerated++ % shards != shard) continue;
                scanned++;
                JsonArray sites=new JsonArray();
                for(Instruction instruction:currentProgram.getListing().getInstructions(function.getBody(),true)) {
                    if(instruction.getFlowType().isComputed() &&
                       (instruction.getFlowType().isCall() || instruction.getFlowType().isJump()) &&
                       instruction.getFlows().length==0) sites.add(instruction.getAddress().toString());
                }
                if(sites.isEmpty()) continue;
                JsonObject entry=new JsonObject(); entry.addProperty("address",function.getEntryPoint().toString());
                entry.add("sites",sites); selected.add(entry);
            }
            request.add("functions",selected);
        }
        Path output = Paths.get(args[1]), partial = Paths.get(args[1] + ".partial");
        if (Files.exists(output) || Files.exists(partial)) throw new IOException("Refusing evidence overwrite");
        directory = output.toAbsolutePath().getParent(); Files.createDirectories(directory);
        limit = Long.parseLong(args[2]);
        if (limit < 1) throw new IllegalArgumentException("Output budget must be positive");
        DecompInterface dec = new DecompInterface();
        DecompileOptions options = new DecompileOptions(); options.grabFromProgram(currentProgram);
        dec.setOptions(options); dec.toggleCCode(!preparation.isEmpty()); dec.toggleSyntaxTree(true);
        if (!dec.openProgram(currentProgram)) throw new IOException(dec.getLastMessage());
        int attempted = 0, ok = 0, failed = 0, timeouts = 0;
        try (BufferedWriter stream = Files.newBufferedWriter(partial, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW)) {
            writer = stream;
            JsonObject header = new JsonObject(); header.addProperty("kind", "program");
            header.add("sha256", request.get("sha256")); header.add("snapshot_id", request.get("snapshot_id"));
            if(!preparation.isEmpty()) header.add("private_preparation",preparation);
            if(request.has("scan_shards")) {
                header.add("scan_shards",request.get("scan_shards")); header.add("scan_shard",request.get("scan_shard"));
                header.addProperty("scanned_functions",scanned); header.addProperty("total_enumerated",enumerated);
                header.addProperty("selected_functions",request.getAsJsonArray("functions").size());
                header.addProperty("scope","Computed transfers inside function bodies; orphan-instruction audit remains separate");
            }
            header.addProperty("analysis_stage",System.getProperty("dragonshard.analysis.stage","manifest_audited_source"));
            header.addProperty("purpose", "Complete backward SSA slices for unresolved computed transfers"); emit(header);
            if (request.has("raw_sites"))
                for (JsonElement site : request.getAsJsonArray("raw_sites")) emit(context(site.getAsString()));
            for (JsonElement item : request.getAsJsonArray("functions")) {
                monitor.checkCancelled(); JsonObject entry = item.getAsJsonObject();
                Address address = toAddr(entry.get("address").getAsString());
                Function function = currentProgram.getFunctionManager().getFunctionAt(address);
                if (function == null) throw new IOException("Missing requested function " + address);
                JsonObject record = new JsonObject(); record.addProperty("kind", "function");
                record.addProperty("address", address.toString()); record.addProperty("name", function.getName(true));
                record.add("requested_sites", entry.get("sites")); attempted++;
                DecompileResults result = dec.decompileFunction(function, 120, monitor);
                record.addProperty("error", result.getErrorMessage());
                if(result.getDecompiledFunction()!=null) record.addProperty("c",result.getDecompiledFunction().getC());
                if (result.decompileCompleted() && result.getHighFunction() != null) {
                    record.addProperty("status", "ok");
                    record.add("slice", slice(result.getHighFunction(), entry.getAsJsonArray("sites"), monitor)); ok++;
                } else {
                    record.addProperty("status", result.isTimedOut() ? "timeout" : "failed");
                    if (result.isTimedOut()) timeouts++; else failed++;
                }
                if (request.has("audit_requested_sites") && request.get("audit_requested_sites").getAsBoolean()) {
                    record.add("instruction_evidence", instructions(entry.getAsJsonArray("sites"), function));
                    record.add("normalization", normalize(function, entry.getAsJsonArray("sites"), request));
                }
                emit(record); stream.flush();
            }
            JsonObject end = new JsonObject(); end.addProperty("kind", "complete");
            end.addProperty("attempted", attempted); end.addProperty("ok", ok);
            end.addProperty("failed", failed); end.addProperty("timeout", timeouts); emit(end);
        } finally { dec.dispose(); }
        Files.move(partial, output, StandardCopyOption.ATOMIC_MOVE);
    }
}
