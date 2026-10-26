// Retry reviewed, bounded image-relative switches on a private packed database only.
// @category DragonShard
import com.google.gson.*;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.pcode.JumpTable;
import ghidra.program.model.symbol.*;
import java.io.IOException;
import java.util.*;

public class DragonShardSwitchRecovery extends DragonShardExceptionEvidence {
    private AddressSet verifyBytes(JsonObject check) throws Exception {
        Address address = toAddr(check.get("address").getAsString());
        byte[] expected = HexFormat.of().parseHex(check.get("hex").getAsString());
        if (expected.length == 0) throw new IOException("Empty byte verification range");
        byte[] actual = new byte[expected.length];
        if (currentProgram.getMemory().getBytes(address, actual) != actual.length ||
            !Arrays.equals(expected, actual)) throw new IOException("Reviewed bytes differ at " + address);
        return new AddressSet(address, address.add(expected.length - 1));
    }
    @Override
    protected JsonArray prepare(Function function, JsonObject request) throws Exception {
        if (System.getProperty("dragonshard.packed.path") == null)
            throw new IOException("Switch recovery requires an isolated packed-program view");
        JsonArray evidence = new JsonArray();
        int tx = currentProgram.startTransaction("Reviewed static switch recovery");
        boolean success = false;
        try {
            for (JsonElement element : request.getAsJsonArray("reviewed_switches")) {
                JsonObject spec = element.getAsJsonObject();
                if (!spec.get("function").getAsString().equals(function.getEntryPoint().toString())) continue;
                AddressSet checked = new AddressSet();
                for (JsonElement bytes : spec.getAsJsonArray("byte_checks"))
                    checked.add(verifyBytes(bytes.getAsJsonObject()));
                Address branch = toAddr(spec.get("branch").getAsString());
                Instruction ins = getInstructionAt(branch);
                if (ins == null || !ins.getFlowType().isJump() || !ins.getFlowType().isComputed())
                    throw new IOException("Not a computed jump: " + branch);
                Address table = toAddr(spec.get("table").getAsString());
                Address map = spec.has("index_map") ? toAddr(spec.get("index_map").getAsString()) : null;
                int count = spec.get("input_count").getAsInt();
                if (count < 1 || count > 4096) throw new IOException("Unbounded reviewed switch");
                MemoryBlock block = currentProgram.getMemory().getBlock(table);
                if (block == null || block.isWrite()) throw new IOException("Table is not immutable image data");
                if (map != null) {
                    MemoryBlock mb = currentProgram.getMemory().getBlock(map);
                    if (mb == null || mb.isWrite()) throw new IOException("Index map is not immutable image data");
                }
                LinkedHashSet<Address> destinations = new LinkedHashSet<>();
                JsonArray cases = new JsonArray();
                AddressSet starts = new AddressSet();
                AddressSet allowed = new AddressSet(toAddr(spec.get("code_start").getAsString()),
                                                    toAddr(spec.get("code_end_exclusive").getAsString()).subtract(1));
                if (!allowed.contains(function.getEntryPoint()) || !allowed.contains(branch))
                    throw new IOException("Switch lies outside reviewed code range");
                if (!checked.contains(allowed)) throw new IOException("Reviewed code range lacks byte verification");
                if (map != null && !checked.contains(map, map.add(count - 1)))
                    throw new IOException("Index map lacks byte verification");
                for (int i = 0; i < count; ++i) {
                    int index = map == null ? i : Byte.toUnsignedInt(currentProgram.getMemory().getByte(map.add(i)));
                    Address slot = table.add(index * 4L);
                    if (!checked.contains(slot, slot.add(3))) throw new IOException("RVA table lacks byte verification");
                    long rva = Integer.toUnsignedLong(currentProgram.getMemory().getInt(slot));
                    Address target = currentProgram.getImageBase().add(rva);
                    MemoryBlock targetBlock = currentProgram.getMemory().getBlock(target);
                    Function owner = getFunctionContaining(target);
                    if (!allowed.contains(target) || targetBlock == null || !targetBlock.isExecute() ||
                        (owner != null && !owner.equals(function)))
                        throw new IOException("Target outside reviewed function: " + target);
                    JsonObject row = new JsonObject(); row.addProperty("input_index", i);
                    row.addProperty("table_index", index); row.addProperty("target", target.toString()); cases.add(row);
                    destinations.add(target); starts.add(target);
                }
                new JumpTable(branch, new ArrayList<>(destinations), true, 0).writeOverride(function);
                for (Address target : destinations)
                    currentProgram.getReferenceManager().addMemoryReference(branch, target,
                        RefType.COMPUTED_JUMP, SourceType.USER_DEFINED, 0);
                DisassembleCommand disassemble = new DisassembleCommand(starts, allowed);
                disassemble.applyTo(currentProgram, monitor);
                for (Address target : destinations)
                    if (getInstructionAt(target) == null) throw new IOException("Undisassembled target " + target);
                JsonObject record = spec.deepCopy(); record.add("enumerated_targets", cases);
                record.addProperty("byte_checks_verified", true);
                record.addProperty("changes_only_in_private_program", true);
                record.addProperty("unique_targets", destinations.size()); evidence.add(record);
            }
            if (evidence.isEmpty()) throw new IOException("No reviewed switches for function");
            CreateFunctionCmd.fixupFunctionBody(currentProgram, function, monitor);
            success = true;
        } finally { currentProgram.endTransaction(tx, success); }
        return evidence;
    }
}
