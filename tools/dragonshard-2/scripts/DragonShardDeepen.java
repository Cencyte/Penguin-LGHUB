// Audit real listing depth and populate isolated research copies before full-corpus export.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import com.google.gson.*;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;

public class DragonShardDeepen extends GhidraScript {
    private AddressSet executable() {
        AddressSet set = new AddressSet();
        for (MemoryBlock b : currentProgram.getMemory().getBlocks())
            if (b.isExecute() && b.isInitialized()) set.add(b.getStart(), b.getEnd());
        return set;
    }
    private JsonObject audit() throws Exception {
        JsonObject result = new JsonObject();
        Listing listing = currentProgram.getListing();
        AddressSet exec = executable(), bodies = new AddressSet(), instructions = new AddressSet();
        int functions = 0, oneByte = 0, missingEntryInstruction = 0, syntheticExternalThunks = 0;
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
            monitor.checkCancelled(); functions++;
            if (f.getBody().getNumAddresses() == 1) oneByte++;
            if (listing.getInstructionAt(f.getEntryPoint()) == null) {
                missingEntryInstruction++;
                Function target = f.isThunk() ? f.getThunkedFunction(true) : null;
                if (target != null && target.isExternal()) syntheticExternalThunks++;
            }
            bodies.add(f.getBody());
        }
        long instructionCount = 0, references = 0, calls = 0, unresolvedCalls = 0;
        for (Instruction ins : listing.getInstructions(exec, true)) {
            monitor.checkCancelled(); instructionCount++;
            instructions.add(ins.getMinAddress(), ins.getMaxAddress());
            if (ins.getFlowType().isCall() && ins.getFlows().length == 0) unresolvedCalls++;
            for (Reference r : ins.getReferencesFrom()) {
                references++;
                if (r.getReferenceType().isCall()) calls++;
            }
        }
        AddressSet data = new AddressSet();
        for (Data d : listing.getDefinedData(exec, true)) data.add(d.getMinAddress(), d.getMaxAddress());
        result.addProperty("functions", functions);
        result.addProperty("one_byte_bodies", oneByte);
        result.addProperty("functions_without_entry_instruction", missingEntryInstruction);
        result.addProperty("synthetic_external_thunks_without_instructions", syntheticExternalThunks);
        result.addProperty("executable_initialized_bytes", exec.getNumAddresses());
        result.addProperty("instruction_count", instructionCount);
        result.addProperty("executable_instruction_bytes", instructions.intersect(exec).getNumAddresses());
        result.addProperty("executable_function_body_bytes", bodies.intersect(exec).getNumAddresses());
        result.addProperty("instructions_outside_function_bodies_bytes", instructions.subtract(bodies).getNumAddresses());
        result.addProperty("undefined_executable_bytes", exec.subtract(instructions).subtract(data).getNumAddresses());
        result.addProperty("instruction_references", references);
        result.addProperty("direct_call_references", calls);
        result.addProperty("unresolved_indirect_call_instructions", unresolvedCalls);
        result.addProperty("symbols", currentProgram.getSymbolTable().getNumSymbols());
        result.addProperty("bookmarks", currentProgram.getBookmarkManager().getBookmarkCount());
        return result;
    }
    private void saveReport(Path path, JsonObject report) throws Exception {
        Path temporary = Paths.get(path.toString() + ".partial");
        Files.writeString(temporary, new GsonBuilder().setPrettyPrinting().create().toJson(report) + "\n");
        Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 3) throw new IllegalArgumentException("report.json expected_sha256 audit|analyze");
        if (!args[1].equalsIgnoreCase(currentProgram.getExecutableSHA256())) throw new IllegalStateException("Binary hash mismatch");
        if (!args[2].equals("audit") && !args[2].equals("analyze")) throw new IllegalArgumentException("Unknown mode");
        Path reportPath = Paths.get(args[0]);
        if (Files.exists(reportPath)) throw new IllegalStateException("Refusing report overwrite");
        JsonObject report = new JsonObject();
        report.addProperty("sha256", currentProgram.getExecutableSHA256());
        report.addProperty("program", currentProgram.getName());
        report.addProperty("project_path", currentProgram.getDomainFile().getPathname());
        report.addProperty("started", Instant.now().toString());
        report.addProperty("mode", args[2]);
        report.addProperty("complete", false);
        report.add("analysis_options", new Gson().toJsonTree(getCurrentAnalysisOptionsAndValues(currentProgram)));
        report.add("before", audit());
        saveReport(reportPath, report);
        println("DEPTH_BEFORE " + report.get("before"));
        if (args[2].equals("analyze")) {
            // The caller must provide a writable isolated project; never use the user's GUI project.
            AddressSet starts = new AddressSet();
            for (Function f : currentProgram.getFunctionManager().getFunctions(true))
                if (currentProgram.getListing().getInstructionAt(f.getEntryPoint()) == null)
                    starts.add(f.getEntryPoint());
            report.addProperty("disassembly_seed_count", starts.getNumAddresses());
            if (!starts.isEmpty()) {
                DisassembleCommand cmd = new DisassembleCommand(starts, executable(), true);
                cmd.enableCodeAnalysis(false);
                report.addProperty("disassembly_command_ok", cmd.applyTo(currentProgram, monitor));
                report.addProperty("disassembly_status", cmd.getStatusMsg());
            }
            println("DISASSEMBLY_SEEDED " + starts.getNumAddresses());
            int fixed = 0, refused = 0;
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled();
                if (f.getBody().getNumAddresses() == 1 && currentProgram.getListing().getInstructionAt(f.getEntryPoint()) != null) {
                    if (CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor)) fixed++;
                    else refused++;
                }
            }
            report.addProperty("body_fixups", fixed);
            report.addProperty("body_fixups_refused", refused);
            report.addProperty("phase", "auto_analysis"); saveReport(reportPath, report);
            println("AUTO_ANALYSIS_START fixed=" + fixed + " refused=" + refused);
            analyzeAll(currentProgram);
            monitor.checkCancelled();
            // Pending analysis may repair bodies and discover additional functions/references.
            analyzeChanges(currentProgram);
        }
        report.add("after", audit());
        report.addProperty("finished", Instant.now().toString());
        report.addProperty("complete", true);
        saveReport(reportPath, report);
        println("DEPTH_AFTER " + report.get("after"));
    }
}
