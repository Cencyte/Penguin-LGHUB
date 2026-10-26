// Independent constant-propagation databases, with explicit three-way conflict detection.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.plugin.core.analysis.X86Analyzer;
import ghidra.framework.data.*;
import ghidra.framework.model.*;
import ghidra.framework.options.Options;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.data.Undefined;
import ghidra.program.model.symbol.*;
import ghidra.program.util.*;
import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.nio.channels.*;
import java.util.*;

public class DragonShardAnalyze extends GhidraScript {
    private final Gson json = new GsonBuilder().setPrettyPrinting().create();
    private JsonObject request;
    private Path output;
    private JsonObject report = new JsonObject();

    private void writeReport(boolean complete) throws Exception {
        report.addProperty("complete", complete);
        Path temporary = output.resolve("report.json.partial");
        Files.writeString(temporary, json.toJson(report) + "\n");
        Files.move(temporary, output.resolve("report.json"), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
    private void identity(Program program) throws Exception {
        if (!request.get("sha256").getAsString().equalsIgnoreCase(program.getExecutableSHA256()))
            throw new IOException("Binary identity mismatch");
    }
    private Program unpack(String path) throws Exception {
        // PackedDatabase.open() has a read-only backing file; every JVM has its OWN DBHandle,
        // changes, caches and locks. Only the immutable unpacking is shared between workers.
        boolean baseline = (request.has("baseline") && request.get("baseline").getAsString().equals(path)) ||
            (request.has("comparison_baseline") && request.get("comparison_baseline").getAsString().equals(path));
        if (request.has("cache_paths"))
            for (JsonElement entry : request.getAsJsonArray("cache_paths"))
                baseline |= entry.getAsString().equals(path);
        PackedDatabase packed = PackedDatabase.getPackedDatabase(new File(path), !baseline, monitor);
        db.DBHandle handle = null;
        try {
            handle = packed.open(monitor);
            Program result = new ProgramDB(handle, OpenMode.UPDATE, monitor, this);
            identity(result);
            return result;
        } catch (Exception exception) {
            if (handle != null) handle.close(); else packed.dispose();
            throw exception;
        }
    }
    private void warmCache() throws Exception {
        // A cold Ghidra cache holds its global 30-second lock during decompression.
        // Populate immutable views before starting the independent analysis JVMs.
        JsonArray warmed = new JsonArray();
        for (JsonElement entry : request.getAsJsonArray("cache_paths")) {
            String path = entry.getAsString();
            Program program = unpack(path);
            try { warmed.add(path); }
            finally { program.release(this); }
            report.add("warmed_paths", warmed); writeReport(false);
        }
    }
    private void pack(Program program) throws Exception {
        report.addProperty("phase", "waiting_to_pack"); writeReport(false);
        // Independent databases may save concurrently. The coordinator admits temporary
        // full-DB copies using measured free space and outstanding output commitments.
        try (FileChannel channel = FileChannel.open(Paths.get(request.get("pack_lock").getAsString()),
                StandardOpenOption.CREATE, StandardOpenOption.WRITE)) {
        FileLock permit = null;
        while (permit == null) {
            monitor.checkCancelled();
            int slots = 1;
            if (request.has("pack_control")) {
                JsonObject control = JsonParser.parseString(Files.readString(Paths.get(request.get("pack_control").getAsString()))).getAsJsonObject();
                slots = control.get("slots").getAsInt();
            }
            for (int i = 0; i < slots && permit == null; i++) permit = channel.tryLock(i, 1, false);
            if (permit == null) Thread.sleep(100);
        }
        try (FileLock lease = permit) {
        report.addProperty("phase", "packing"); writeReport(false);
        long maximum = request.get("max_output_bytes").getAsLong();
        if (Files.getFileStore(output).getUsableSpace() < maximum)
            throw new IOException("Space no longer covers this task's packed-output commitment");
        Path partial = output.resolve("program.gzf.partial");
        program.saveToPackedFile(partial.toFile(), monitor);
        if (Files.size(partial) > maximum) throw new IOException("Packed output exceeded task budget");
        Files.move(partial, output.resolve("program.gzf"), StandardCopyOption.ATOMIC_MOVE);
        report.addProperty("packed_bytes", Files.size(output.resolve("program.gzf")));
        }
        }
    }
    private AddressSet executable(Program program) {
        AddressSet set = new AddressSet();
        for (MemoryBlock block : program.getMemory().getBlocks())
            if (block.isExecute() && block.isInitialized()) set.add(block.getStart(), block.getEnd());
        return set;
    }
    private JsonArray encode(AddressSetView set) {
        JsonArray ranges = new JsonArray();
        for (AddressRange range : set.getAddressRanges(true)) {
            JsonArray pair = new JsonArray(); pair.add(range.getMinAddress().toString()); pair.add(range.getMaxAddress().toString()); ranges.add(pair);
        }
        return ranges;
    }
    private AddressSet decode(Program program, JsonArray ranges) {
        AddressSet set = new AddressSet();
        for (JsonElement entry : ranges) {
            JsonArray pair = entry.getAsJsonArray();
            set.add(program.getAddressFactory().getAddress(pair.get(0).getAsString()),
                    program.getAddressFactory().getAddress(pair.get(1).getAsString()));
        }
        return set;
    }
    private AddressSet differences(Program one, Program two, AddressSetView scope) throws Exception {
        return new AddressSet(new ProgramDiff(one, two, scope).getDifferences(new ProgramDiffFilter(ProgramDiffFilter.ALL_DIFFS), monitor));
    }
    private JsonArray inventory(Program program) throws Exception {
        JsonArray functions = new JsonArray();
        for (Function function : program.getFunctionManager().getFunctions(true)) {
            monitor.checkCancelled();
            if (program.getListing().getInstructionAt(function.getEntryPoint()) == null) continue;
            JsonObject row = new JsonObject(); row.addProperty("address", function.getEntryPoint().toString());
            row.addProperty("body_bytes", function.getBody().getNumAddresses()); functions.add(row);
        }
        Files.writeString(output.resolve("functions.json"), json.toJson(functions) + "\n", StandardOpenOption.CREATE_NEW);
        return functions;
    }
    private void checkpoint() throws Exception {
        DefaultProjectData project = new DefaultProjectData(new ProjectLocator(
            request.get("source_dir").getAsString(), request.get("source_name").getAsString()), false, false);
        Program program = null;
        try {
            DomainFile file = project.getFile(request.get("program_path").getAsString());
            if (file == null) throw new IOException("Source program missing");
            long modified = file.getLastModifiedTime();
            program = (Program)file.getReadOnlyDomainObject(this, DomainFile.DEFAULT_VERSION, monitor);
            identity(program);
            int transaction = program.startTransaction("Disassembly checkpoint on private read-only program object");
            boolean success = false;
            try {
                AddressSet starts = new AddressSet();
                for (Function function : program.getFunctionManager().getFunctions(true)) {
                    monitor.checkCancelled();
                    if (program.getListing().getInstructionAt(function.getEntryPoint()) == null)
                        starts.add(function.getEntryPoint());
                }
                report.addProperty("seed_entries", starts.getNumAddresses()); writeReport(false);
                if (!starts.isEmpty()) {
                    DisassembleCommand disassemble = new DisassembleCommand(starts, executable(program), true);
                    disassemble.enableCodeAnalysis(false);
                    report.addProperty("disassembly_command_ok", disassemble.applyTo(program, monitor));
                }
                int fixed = 0, refused = 0;
                for (Function function : program.getFunctionManager().getFunctions(true)) {
                    monitor.checkCancelled();
                    if (function.getBody().getNumAddresses() == 1 && program.getListing().getInstructionAt(function.getEntryPoint()) != null) {
                        if (CreateFunctionCmd.fixupFunctionBody(program, function, monitor)) fixed++; else refused++;
                    }
                }
                report.addProperty("body_fixups", fixed); report.addProperty("body_fixups_refused", refused);
                if (request.has("validation_strip_references") && request.get("validation_strip_references").getAsBoolean()) {
                    int removed = 0;
                    for (Instruction instruction : program.getListing().getInstructions(executable(program), true)) {
                        monitor.checkCancelled();
                        for (Reference reference : instruction.getReferencesFrom()) {
                            if (reference.isMemoryReference() && reference.getSource() != SourceType.USER_DEFINED &&
                                reference.getSource() != SourceType.IMPORTED) {
                                program.getReferenceManager().delete(reference); removed++;
                            }
                        }
                    }
                    report.addProperty("validation_only_removed_derived_references", removed);
                }
                success = true;
            } finally { program.endTransaction(transaction, success); }
            JsonArray functions = inventory(program);
            report.addProperty("functions", functions.size());
            if (modified != file.getLastModifiedTime()) throw new IOException("Original source changed during checkpoint");
            pack(program);
            report.addProperty("original_source_unchanged", true);
            report.addProperty("scope", "Disassembly checkpoint; not full auto-analysis");
        } finally { if (program != null) program.release(this); project.close(); }
    }
    private void prepareComparisonBaseline() throws Exception {
        if (request.has("comparison_baseline")) return;
        // Compatibility with already-running coordinators: new worker JVMs can acquire
        // this immutable comparison view without restarting the producer/controller.
        Path directory = Paths.get(request.get("pack_lock").getAsString()).getParent();
        Path copy = directory.resolve("comparison-" + request.get("baseline_sha256").getAsString() + ".gzf");
        try (FileChannel channel = FileChannel.open(directory.resolve("comparison-copy.lock"),
                StandardOpenOption.CREATE, StandardOpenOption.WRITE); FileLock lock = channel.lock()) {
            if (!Files.exists(copy)) {
                Path partial = copy.resolveSibling(copy.getFileName() + ".partial");
                Files.copy(Paths.get(request.get("baseline").getAsString()), partial);
                Files.move(partial, copy, StandardCopyOption.ATOMIC_MOVE);
            }
        }
        request.addProperty("comparison_baseline", copy.toString());
    }
    private void worker() throws Exception {
        Program program = unpack(request.get("baseline").getAsString());
        try {
            X86Analyzer analyzer = new X86Analyzer();
            if (!analyzer.canAnalyze(program)) throw new IOException("This pass currently supports x86 only");
            int transaction = program.startTransaction("Independent constant-propagation shard");
            boolean success = false;
            try {
                Options options = program.getOptions(Program.ANALYSIS_PROPERTIES).getOptions(analyzer.getName());
                analyzer.registerOptions(options, program); analyzer.optionsChanged(options, program);
                int attempted = 0;
                for (JsonElement entry : request.getAsJsonArray("functions")) {
                    monitor.checkCancelled();
                    Address address = program.getAddressFactory().getAddress(entry.getAsString());
                    Function function = program.getFunctionManager().getFunctionAt(address);
                    if (function == null || program.getListing().getInstructionAt(address) == null)
                        throw new IOException("Missing assigned function/instruction: " + address);
                    report.addProperty("current_function", address.toString());
                    if (attempted % 16 == 0) { report.addProperty("attempted", attempted); writeReport(false); }
                    analyzer.analyzeLocation(program, address, function.getBody(), monitor);
                    attempted++;
                }
                report.addProperty("attempted", attempted);
                report.add("assigned_functions", request.get("functions"));
                success = true;
            } finally { program.endTransaction(transaction, success); }
            AddressSet changed = program.getChanges().getAddressSetCollectionSinceLastSave().getCombinedAddressSet();
            report.add("changed_ranges", encode(changed));
            report.addProperty("changed_address_bytes", changed.getNumAddresses());
            prepareComparisonBaseline();
            if (request.has("comparison_baseline")) {
                report.addProperty("phase", "computing_database_diff"); writeReport(false);
                Program comparison = unpack(request.get("comparison_baseline").getAsString());
                try {
                    AddressSet actual = changed.isEmpty() ? new AddressSet() : differences(comparison, program, changed);
                    report.add("actual_changed_ranges", encode(actual));
                    report.addProperty("actual_diff_filter", ProgramDiffFilter.ALL_DIFFS);
                    report.addProperty("actual_changed_address_bytes", actual.getNumAddresses());
                } finally { comparison.release(this); }
            }
            pack(program);
            report.addProperty("scope", "X86Analyzer.analyzeLocation for all assigned functions; other analyzers are separate");
        } finally { program.release(this); }
    }
    private boolean onlyUndefined(Program program, AddressSetView set) {
        for (AddressRange range : set.getAddressRanges(true)) {
            Address address = range.getMinAddress();
            while (address != null && address.compareTo(range.getMaxAddress()) <= 0) {
                Data data = program.getListing().getDataContaining(address);
                if (data == null || !Undefined.isUndefined(data.getDataType())) return false;
                address = data.getMaxAddress().next();
            }
        }
        return true;
    }
    private JsonArray dataLayout(Program program, AddressSetView set) {
        JsonArray result = new JsonArray();
        for (Data data : program.getListing().getData(set, true)) {
            JsonObject item = new JsonObject(); item.addProperty("address", data.getMinAddress().toString());
            item.addProperty("length", data.getLength()); item.addProperty("type", data.getDataType().getPathName());
            result.add(item);
        }
        return result;
    }
    private AddressSet retainWidthAnnotations(Program baseline, Program merged, Program worker,
            AddressSetView scope, JsonArray observations) throws Exception {
        AddressSet retained = new AddressSet();
        AddressSet reviewedUntyped = reviewedUntypedWidthRanges(baseline);
        ProgramDiff diff = new ProgramDiff(merged, worker, scope);
        AddressSet code = new AddressSet(diff.getDifferences(new ProgramDiffFilter(ProgramDiffFilter.CODE_UNIT_DIFFS), monitor));
        AddressSet other = new AddressSet(diff.getDifferences(new ProgramDiffFilter(
            ProgramDiffFilter.ALL_DIFFS & ~ProgramDiffFilter.CODE_UNIT_DIFFS), monitor));
        // Keep established access-width hints; never turn an undefined placeholder into
        // a claimed type. Record every alternative. Real types, bytes and all other
        // differing properties remain subject to the ordinary conflict check.
        for (AddressRange range : code.getAddressRanges(true)) {
            AddressSet region = new AddressSet(range.getMinAddress(), range.getMaxAddress());
            if (region.intersects(other) || !onlyUndefined(baseline, region) ||
                !onlyUndefined(merged, region) || !onlyUndefined(worker, region)) continue;
            boolean established = false;
            for (Data data : merged.getListing().getDefinedData(region, true)) {
                if (Undefined.isUndefined(data.getDataType())) { established = true; break; }
            }
            // getDefinedData starts at region minima; include a containing offcut unit.
            Data containing = merged.getListing().getDefinedDataContaining(range.getMinAddress());
            established |= containing != null && Undefined.isUndefined(containing.getDataType());
            // Some other analyzers repeatedly clear an access-width hint. Permit
            // retaining the untyped baseline only for explicitly reviewed bytes.
            // All three programs must still be undefined-only, with no other diff.
            if (!established && !reviewedUntyped.contains(region)) continue;
            JsonObject observation = new JsonObject(); observation.add("ranges", encode(region));
            observation.addProperty("retention_basis", established ? "established_undefined_width" : "reviewed_undefined_width_cycle");
            observation.add("retained", dataLayout(merged, region));
            observation.add("alternative", dataLayout(worker, region)); observations.add(observation);
            retained.add(region);
        }
        return retained;
    }
    private AddressSet reviewedUntypedWidthRanges(Program program) throws Exception {
        AddressSet reviewed = new AddressSet();
        if (!request.has("reviewed_untyped_widths")) return reviewed;
        for (JsonElement element : request.getAsJsonArray("reviewed_untyped_widths")) {
            JsonObject item = element.getAsJsonObject();
            Address address = program.getAddressFactory().getAddress(item.get("address").getAsString());
            byte[] expected = HexFormat.of().parseHex(item.get("hex").getAsString());
            if (address == null || expected.length == 0 || expected.length > 4096 ||
                !item.has("reason") || item.get("reason").getAsString().isBlank())
                throw new IOException("Invalid reviewed width range");
            byte[] actual = new byte[expected.length];
            if (program.getMemory().getBytes(address, actual) != actual.length || !Arrays.equals(expected, actual))
                throw new IOException("Reviewed width bytes differ at " + address);
            reviewed.add(address, address.add(expected.length - 1));
        }
        return reviewed;
    }
    private Path awaitWorker(List<Path> pending) throws Exception {
        while (true) {
            monitor.checkCancelled();
            for (Path folder : pending) {
                Path path = folder.resolve("report.json");
                if (!Files.exists(path)) continue;
                JsonObject value = JsonParser.parseString(Files.readString(path)).getAsJsonObject();
                if (value.has("error")) throw new IOException("Worker failed: " + value.get("error"));
                if (value.get("complete").getAsBoolean()) return folder;
            }
            report.addProperty("phase", "waiting_for_saved_worker");
            report.addProperty("workers_pending", pending.size()); writeReport(false);
            Thread.sleep(250);
        }
    }
    private void merge() throws Exception {
        Program baseline = unpack(request.get("baseline").getAsString());
        // A distinct packed path produces a distinct cache/DBHandle inside this JVM as well.
        Program merged = unpack(request.get("merge_base").getAsString());
        try {
            reviewedUntypedWidthRanges(baseline);
            if (request.has("reviewed_untyped_widths")) report.add("reviewed_untyped_widths", request.get("reviewed_untyped_widths"));
            AddressSet accumulated = new AddressSet(); JsonArray steps = new JsonArray();
            int transaction = merged.startTransaction("Three-way parallel analysis merge");
            boolean success = false;
            try {
                List<Path> pending = new ArrayList<>();
                for (JsonElement element : request.getAsJsonArray("workers")) pending.add(Paths.get(element.getAsString()));
                while (!pending.isEmpty()) {
                    Path folder = awaitWorker(pending); pending.remove(folder);
                    JsonObject workerReport = JsonParser.parseString(Files.readString(folder.resolve("report.json"))).getAsJsonObject();
                    report.addProperty("phase", "verifying_and_merging");
                    if (!workerReport.get("complete").getAsBoolean() ||
                        !workerReport.get("baseline_sha256").getAsString().equals(request.get("baseline_sha256").getAsString()))
                        throw new IOException("Incomplete or different-checkpoint worker");
                    Program worker = unpack(folder.resolve("program.gzf").toString());
                    try {
                        AddressSet changed = decode(baseline, workerReport.getAsJsonArray("changed_ranges"));
                        AddressSet actual;
                        if (workerReport.has("actual_changed_ranges") && workerReport.get("actual_diff_filter").getAsInt() == ProgramDiffFilter.ALL_DIFFS) {
                            actual = decode(baseline, workerReport.getAsJsonArray("actual_changed_ranges"));
                        } else actual = changed.isEmpty() ? new AddressSet() : differences(baseline, worker, changed);
                        JsonArray widths = new JsonArray();
                        AddressSet retained = request.has("retain_access_widths") && request.get("retain_access_widths").getAsBoolean()
                            ? retainWidthAnnotations(baseline, merged, worker, actual, widths) : new AddressSet();
                        actual.delete(retained);
                        AddressSet overlap = actual.intersect(accumulated);
                        AddressSet conflicts = overlap.isEmpty() ? new AddressSet() : differences(merged, worker, overlap);
                        JsonObject step = new JsonObject(); step.addProperty("worker", folder.toString());
                        step.addProperty("changed_bytes", actual.getNumAddresses()); step.add("conflicts", encode(conflicts));
                        step.add("retained_access_width_alternatives", widths);
                        steps.add(step); report.add("steps", steps); writeReport(false);
                        if (!conflicts.isEmpty()) throw new IOException("Conflicting worker mutations; refusing lossy merge");
                        if (!actual.isEmpty()) {
                            ProgramMergeManager merger = new ProgramMergeManager(merged, worker, actual);
                            if (!merger.merge(actual, new ProgramMergeFilter(ProgramMergeFilter.ALL, ProgramMergeFilter.REPLACE), monitor))
                                throw new IOException("Merge failed: " + merger.getErrorMessage());
                            AddressSet residual = differences(merged, worker, actual);
                            if (!residual.isEmpty()) throw new IOException("Merged result differs from worker on changed addresses");
                            accumulated.add(actual);
                        }
                    } finally { worker.release(this); }
                }
                success = true;
            } finally { merged.endTransaction(transaction, success); }
            AddressSet netChanges = accumulated.isEmpty() ? new AddressSet() : differences(baseline, merged, accumulated);
            report.addProperty("changed_address_bytes", netChanges.getNumAddresses());
            report.add("changed_ranges", encode(netChanges));
            report.addProperty("access_width_policy", "preserve established undefined-width annotations; retain alternatives in steps");
            pack(merged);
        } finally { merged.release(this); baseline.release(this); }
    }
    private void inspect() throws Exception {
        Program baseline = unpack(request.get("baseline").getAsString());
        Program one = unpack(request.get("one").getAsString());
        Program two = unpack(request.get("two").getAsString());
        try {
            AddressSet scope = decode(baseline, request.getAsJsonArray("ranges"));
            if (request.has("reviewed_untyped_widths")) {
                JsonArray observations = new JsonArray();
                AddressSet retained = retainWidthAnnotations(baseline, baseline, one, scope, observations);
                report.add("width_policy_retained_ranges", encode(retained));
                report.add("width_policy_observations", observations);
            }
            JsonArray categories = new JsonArray();
            for (int bit = 1; bit <= ProgramDiffFilter.SOURCE_MAP_DIFFS; bit <<= 1) {
                JsonObject item = new JsonObject(); item.addProperty("category", ProgramDiffFilter.typeToName(bit));
                item.add("base_one", encode(new ProgramDiff(baseline, one, scope).getDifferences(new ProgramDiffFilter(bit), monitor)));
                item.add("base_two", encode(new ProgramDiff(baseline, two, scope).getDifferences(new ProgramDiffFilter(bit), monitor)));
                item.add("one_two", encode(new ProgramDiff(one, two, scope).getDifferences(new ProgramDiffFilter(bit), monitor)));
                categories.add(item);
            }
            report.add("categories", categories);
            JsonArray contents = new JsonArray();
            for (Program program : new Program[]{baseline, one, two}) {
                JsonObject contentsItem = new JsonObject(); JsonArray addresses = new JsonArray();
                for (Address address : scope.getAddresses(true)) {
                    JsonObject item = new JsonObject(); item.addProperty("address", address.toString());
                    item.addProperty("code", String.valueOf(program.getListing().getCodeUnitContaining(address)));
                    JsonArray symbols = new JsonArray();
                    for (Symbol symbol : program.getSymbolTable().getSymbols(address)) symbols.add(symbol.toString() + " source=" + symbol.getSource() + " primary=" + symbol.isPrimary());
                    item.add("symbols", symbols);
                    JsonArray references = new JsonArray();
                    for (Reference reference : program.getReferenceManager().getReferencesFrom(address)) references.add(reference.toString() + " source=" + reference.getSource() + " primary=" + reference.isPrimary());
                    item.add("references", references); addresses.add(item);
                }
                contentsItem.add("addresses", addresses); contents.add(contentsItem);
            }
            report.add("contents_baseline_one_two", contents);
        } finally { two.release(this); one.release(this); baseline.release(this); }
    }
    private void compare() throws Exception {
        Program one = unpack(request.get("one").getAsString()), two = unpack(request.get("two").getAsString());
        try {
            AddressSet differences = differences(one, two, null);
            report.add("differences", encode(differences)); report.addProperty("equal", differences.isEmpty());
            if (!differences.isEmpty()) throw new IOException("Parallel/serial result mismatch");
        } finally { one.release(this); two.release(this); }
    }
    private void validateWidthFixedPoint() throws Exception {
        Program baseline = unpack(request.get("baseline").getAsString());
        Program merged = unpack(request.get("one").getAsString());
        try {
            if (!request.has("reviewed_untyped_widths"))
                throw new IOException("A reviewed width policy is required");
            // Compare the entire saved databases, not just the suspected cycle.
            // The ordinary merge already verified worker identity and overlaps.
            reviewedUntypedWidthRanges(baseline);
            AddressSet changed = differences(baseline, merged, null);
            report.addProperty("comparison_scope", "whole_program");
            JsonArray observations = new JsonArray();
            AddressSet retained = retainWidthAnnotations(baseline, baseline, merged, changed, observations);
            AddressSet remaining = changed.subtract(retained);
            report.add("all_difference_ranges", encode(changed));
            report.add("retained_width_ranges", encode(retained));
            report.add("width_observations", observations);
            report.add("unaccepted_difference_ranges", encode(remaining));
            report.add("reviewed_untyped_widths", request.get("reviewed_untyped_widths"));
            report.addProperty("actual_diff_filter", ProgramDiffFilter.ALL_DIFFS);
            report.addProperty("fixed_point_under_recorded_width_policy", remaining.isEmpty());
            if (!remaining.isEmpty()) throw new IOException("Saved merge contains changes beyond the reviewed width policy");
        } finally { merged.release(this); baseline.release(this); }
    }
    private void finish() throws Exception {
        Program program = unpack(request.get("baseline").getAsString());
        try {
            currentProgram = program;
            int transaction = program.startTransaction("Remaining auto-analysis after parallel constant propagation");
            boolean success = false;
            try {
                Options options = program.getOptions(Program.ANALYSIS_PROPERTIES);
                String name = new X86Analyzer().getName();
                boolean enabled = options.getBoolean(name, true);
                options.setBoolean(name, false);
                try {
                    analyzeAll(program); analyzeChanges(program); monitor.checkCancelled();
                    // Stock ConstantPropagationAnalyzer also visits instructions outside functions.
                    // Keep that tail; never silently omit it from the partitioned implementation.
                    AddressSet orphan = new AddressSet();
                    for (Instruction instruction : program.getListing().getInstructions(executable(program), true))
                        orphan.add(instruction.getMinAddress(), instruction.getMaxAddress());
                    for (Function function : program.getFunctionManager().getFunctions(true)) orphan.delete(function.getBody());
                    report.addProperty("orphan_instruction_bytes", orphan.getNumAddresses()); writeReport(false);
                    if (!orphan.isEmpty()) {
                        X86Analyzer analyzer = new X86Analyzer();
                        Options settings = options.getOptions(analyzer.getName());
                        analyzer.registerOptions(settings, program); analyzer.optionsChanged(settings, program);
                        analyzer.analyzeSet(program, orphan, monitor); analyzeChanges(program);
                    }
                }
                finally { options.setBoolean(name, enabled); }
                success = true;
            } finally { program.endTransaction(transaction, success); }
            report.addProperty("functions", inventory(program).size());
            pack(program);
            DomainFolder folder = state.getProject().getProjectData().getRootFolder();
            String[] parts = request.get("program_path").getAsString().substring(1).split("/");
            for (int i = 0; i < parts.length - 1; i++) folder = folder.createFolder(parts[i]);
            folder.createFile(parts[parts.length - 1], program, monitor);
            JsonObject after = new JsonObject();
            long functions = 0, missing = 0, synthetic = 0, instructionCount = 0, references = 0;
            for (Function function : program.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled(); functions++;
                if (program.getListing().getInstructionAt(function.getEntryPoint()) == null) {
                    missing++; Function target = function.isThunk() ? function.getThunkedFunction(true) : null;
                    if (target != null && target.isExternal()) synthetic++;
                }
            }
            for (Instruction instruction : program.getListing().getInstructions(executable(program), true)) {
                monitor.checkCancelled(); instructionCount++; references += instruction.getReferencesFrom().length;
            }
            after.addProperty("functions", functions); after.addProperty("functions_without_entry_instruction", missing);
            after.addProperty("synthetic_external_thunks_without_instructions", synthetic);
            after.addProperty("instruction_count", instructionCount); after.addProperty("instruction_references", references);
            JsonObject audit = new JsonObject(); audit.add("sha256", request.get("sha256"));
            audit.addProperty("complete", true); audit.addProperty("mode", "parallel_constant_propagation_then_remaining_analyzers");
            audit.add("after", after);
            Files.writeString(output.resolve("depth.json"), json.toJson(audit) + "\n", StandardOpenOption.CREATE_NEW);
            report.addProperty("source_dir", output.resolve("driver").toString());
            report.addProperty("source_name", "Driver"); report.addProperty("audit", output.resolve("depth.json").toString());
        } finally { currentProgram = null; program.release(this); }
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("request.json");
        request = JsonParser.parseString(Files.readString(Paths.get(args[0]))).getAsJsonObject();
        output = Paths.get(request.get("output").getAsString()); Files.createDirectories(output);
        if (Files.exists(output.resolve("report.json"))) throw new IOException("Refusing report overwrite");
        String mode = request.get("mode").getAsString();
        report.addProperty("mode", mode); report.add("sha256", request.get("sha256"));
        if (request.has("baseline_sha256")) report.add("baseline_sha256", request.get("baseline_sha256"));
        writeReport(false);
        try {
            if (mode.equals("checkpoint")) checkpoint();
            else if (mode.equals("warm_cache")) warmCache();
            else if (mode.equals("worker")) worker();
            else if (mode.equals("merge")) merge();
            else if (mode.equals("compare")) compare();
            else if (mode.equals("validate_width_fixed_point")) validateWidthFixedPoint();
            else if (mode.equals("inspect")) inspect();
            else if (mode.equals("finish")) finish();
            else throw new IOException("Unknown mode");
            writeReport(true);
        } catch (Exception exception) {
            report.addProperty("error", exception.toString()); writeReport(false); throw exception;
        }
    }
}
